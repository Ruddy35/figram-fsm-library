package main

// A scenario: users of the three bots against the host, with crash injection (the supervisor
// replaces a crashed instance by a new one that recovers from the store), document reloads,
// duplicated deliveries; then the drain and the end-of-run invariants.

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type ScenarioOpts struct {
	Name          string
	UsersPerBot   int
	Bots          []string
	Goals         int
	MaxActions    int
	Seed          int64
	DB            string
	Sync          string
	ReloadEvery   time.Duration
	NotifyLatency time.Duration
	ReplyWait     time.Duration
	DrainTimeout  time.Duration
	Out           string
}

type ScenarioResult struct {
	Opts        ScenarioOpts             `json:"opts"`
	Control     string                   `json:"control"`
	Checks      []*CheckResult           `json:"checks"`
	OK          bool                     `json:"ok"`
	Crashes     map[string]int64         `json:"crashes"`
	Instances   int                      `json:"instances"`
	Reloads     int64                    `json:"reloads"`
	ClosedRetry int64                    `json:"closed_retries"`
	Counts      map[string]int64         `json:"counts"`
	Elapsed     float64                  `json:"elapsed_s"`
	Drained     bool                     `json:"drained"`
}

func runScenario(cfg *Config, o ScenarioOpts, control string) ScenarioResult {
	start := time.Now()
	protoViolations.Store(0)
	st, err := openStore(o.DB, o.Sync, true)
	if err != nil {
		panic(err)
	}
	defer st.Close()
	var users []string
	for _, b := range o.Bots {
		for i := 0; i < o.UsersPerBot; i++ {
			users = append(users, fmt.Sprintf("%s-%d", b, i))
		}
	}
	sims := newSims(o.Seed, users, o.NotifyLatency)
	sims.tg = newTelegram(o.Seed+9, users)
	crashes := &sync.Map{}
	stats := &Stats{}
	h := &Host{dupProb: cfg.dupProb}
	gen := 0
	in, err := startInstance(gen, cfg, st, sims, crashes, stats, o.Bots)
	if err != nil {
		panic(err)
	}
	h.cur.Store(in)
	var instances atomic.Int64
	instances.Store(1)
	stop := make(chan struct{})
	var supWG sync.WaitGroup
	var restarting atomic.Bool
	var reloads, closedRetries atomic.Int64
	supWG.Add(1)
	go func() { // supervisor: a crashed instance is replaced by a new one recovering from the store
		defer supWG.Done()
		for {
			cur := h.cur.Load()
			select {
			case <-stop:
				return
			case <-cur.died:
			}
			select {
			case <-stop:
				return
			default:
			}
			restarting.Store(true)
			cur.wg.Wait()
			reloads.Add(cur.reg.loads.Load())
			closedRetries.Add(cur.reg.closed.Load())
			cur.reg.CloseAll()
			gen++
			var nin *Instance
			for {
				nin, err = startInstance(gen, cfg, st, sims, crashes, stats, o.Bots)
				if err == nil {
					break
				}
				panic(err)
			}
			h.cur.Store(nin)
			instances.Add(1)
			restarting.Store(false)
		}
	}()
	if o.ReloadEvery > 0 {
		supWG.Add(1)
		go func() { // reloader: the next version of a document while users wait
			defer supWG.Done()
			r := rand.New(rand.NewSource(o.Seed + 77))
			t := time.NewTicker(o.ReloadEvery)
			defer t.Stop()
			for {
				select {
				case <-stop:
					return
				case <-t.C:
				}
				in := h.cur.Load()
				if in.dead() {
					continue
				}
				b := o.Bots[r.Intn(len(o.Bots))]
				cur := in.reg.Current(b)
				if cur == nil {
					continue
				}
				next := b + "@2"
				if strings.HasSuffix(cur.key, "@2") {
					next = b + "@1"
				}
				in.reg.Install(in, next)
			}
		}()
	}
	ust := &UserStats{}
	var uwg sync.WaitGroup
	for i, u := range users {
		su := &SimUser{id: u, h: h, tg: sims.tg, rng: rand.New(rand.NewSource(o.Seed*7919 + int64(i))), scale: cfg.timeScale,
			st: ust, goals: o.Goals, maxActions: o.MaxActions, replyWait: o.ReplyWait}
		uwg.Add(1)
		go func() { defer uwg.Done(); su.run() }()
	}
	progStop := make(chan struct{})
	go func() { // progress on stderr, so that a run stopped by its timeout still says how far it got
		for {
			select {
			case <-progStop:
				return
			case <-time.After(30 * time.Second):
			}
			var a, b, c, d int
			st.r.QueryRow(`SELECT count(*) FROM inbound`).Scan(&a)
			st.r.QueryRow(`SELECT count(*) FROM outbox WHERE state=0`).Scan(&b)
			st.r.QueryRow(`SELECT count(*) FROM steplog`).Scan(&c)
			st.r.QueryRow(`SELECT count(*) FROM ingest_log`).Scan(&d)
			fmt.Fprintf(os.Stderr, "progress t=%.0fs updates=%d steps=%d inbound=%d outbox_pending=%d user_actions=%d no_reply=%d goroutines=%d %s\n",
				time.Since(start).Seconds(), d, c, a, b, ust.actions.Load(), ust.noReply.Load(), runtime.NumGoroutine(), txProf.String())
		}
	}()
	defer close(progStop)
	uwg.Wait()
	usersDone := time.Since(start)
	// drain: nothing in the inbound queue, the outbox or the scheduler, no worker active
	drained := false
	deadline := time.Now().Add(o.DrainTimeout)
	stable := 0
	for time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
		if restarting.Load() {
			stable = 0
			continue
		}
		var a, b, c int
		st.r.QueryRow(`SELECT count(*) FROM inbound`).Scan(&a)
		st.r.QueryRow(`SELECT count(*) FROM outbox WHERE state=0`).Scan(&b)
		st.r.QueryRow(`SELECT count(*) FROM timers`).Scan(&c)
		if a+b+c == 0 {
			stable++
			if stable >= 4 {
				drained = true
				break
			}
		} else {
			stable = 0
		}
	}
	close(stop)
	supWG.Wait()
	in = h.cur.Load()
	in.crash("end-of-run") // stop the last instance (not counted as an injected crash)
	in.wg.Wait()
	reloads.Add(in.reg.loads.Load())
	closedRetries.Add(in.reg.closed.Load())
	in.reg.CloseAll()
	crashes.Delete("end-of-run")
	res := ScenarioResult{Opts: o, Control: control, Instances: int(instances.Load()), Drained: drained,
		Reloads: reloads.Load(), ClosedRetry: closedRetries.Load(), Crashes: map[string]int64{}}
	crashes.Range(func(k, v any) bool { res.Crashes[k.(string)] = v.(*atomic.Int64).Load(); return true })
	res.Checks = runChecks(st, sims, ust, o.NotifyLatency)
	if !drained {
		c := newCheck("drain")
		c.fail("the host did not drain within %v", o.DrainTimeout)
		res.Checks = append(res.Checks, c)
	}
	res.OK = true
	for _, c := range res.Checks {
		res.OK = res.OK && c.OK
	}
	cnt := func(q string) int64 {
		var n int64
		st.w.QueryRow(q).Scan(&n)
		return n
	}
	lat := stats.stepLat
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	pct := func(p int) int64 {
		if len(lat) == 0 {
			return 0
		}
		return lat[len(lat)*p/100]
	}
	res.Counts = map[string]int64{
		"users": int64(len(users)), "updates": h.ups.Load(), "updates_delivered_twice": h.dupsSent.Load(),
		"steps": cnt(`SELECT count(*) FROM steplog`), "outbox": cnt(`SELECT count(*) FROM outbox`),
		"calls_with_token": cnt(`SELECT count(*) FROM outbox WHERE kind='call' AND token IS NOT NULL`),
		"detached_calls": cnt(`SELECT count(*) FROM outbox WHERE detached=1`),
		"outcome_steps": cnt(`SELECT count(*) FROM steplog WHERE event LIKE 'call:%'`),
		"outcomes_outside_waiting_msg": cnt(`SELECT count(*) FROM steplog WHERE event LIKE 'call:%' AND json_extract(pre,'$.at') NOT IN ('placing','placing2','hold')`),
		"order_outcomes_on_other_msg": cnt(`SELECT count(*) FROM steplog WHERE event LIKE 'call:create_order:%' AND json_extract(pre,'$.at') NOT IN ('placing','placing2')`),
		"reserve_outcomes_ignored": cnt(`SELECT count(*) FROM steplog WHERE event LIKE 'call:reserve:%' AND pre=post`),
		"timer_steps": cnt(`SELECT count(*) FROM steplog WHERE event LIKE 'timer:%'`),
		"no_target_steps": cnt(`SELECT count(*) FROM steplog WHERE op='step' AND json_extract(pre,'$.at')=json_extract(post,'$.at') AND actions NOT LIKE '%"send"%' AND json_extract(pre,'$.seq')<>json_extract(post,'$.seq')`),
		"recovered_steps": cnt(`SELECT count(*) FROM steplog WHERE recovered=1`),
		"restarts": cnt(`SELECT count(*) FROM steplog WHERE op='restart'`),
		"starts_with_hwm": cnt(`SELECT count(*) FROM steplog WHERE op='start' AND hwm>0`),
		"lost_snapshots": cnt(`SELECT count(*) FROM steplog WHERE op='lose'`),
		"max_pending": cnt(`SELECT coalesce(max(json_array_length(post,'$.pending')),0) FROM steplog`),
		"drops": cnt(`SELECT count(*) FROM drops`), "drops_no_snapshot": cnt(`SELECT count(*) FROM drops WHERE reason='no snapshot'`),
		"drops_run_id": cnt(`SELECT count(*) FROM drops WHERE reason='run id mismatch'`),
		"dup_filtered": stats.dups.Load(), "reconciliations": stats.recons.Load(), "reconciled_hits": stats.reconHits.Load(),
		"failed_outcomes": stats.failed.Load(), "bg_failq": stats.bgFail.Load(), "seq_failq": stats.seqFail.Load(),
		"crm_effects": sims.crm.effects.Load(), "crm_calls": sims.crm.calls.Load(), "notify_calls": sims.notify.calls.Load(),
		"user_goals": ust.goalsReached.Load(), "user_actions": ust.actions.Load(), "user_no_reply": ust.noReply.Load(),
		"user_support_ops": ust.supportOps.Load(),
		"step_latency_p50_us": pct(50), "step_latency_p99_us": pct(99),
		"users_phase_ms": usersDone.Milliseconds(), "goroutines_at_end": int64(runtime.NumGoroutine()),
	}
	res.Elapsed = time.Since(start).Seconds()
	if o.Out != "" {
		b, _ := json.MarshalIndent(res, "", " ")
		os.WriteFile(o.Out, b, 0o644)
	}
	return res
}
