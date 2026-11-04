package main

// Latency and throughput of `step` with a large `pending` (thousands of `every` waits), near the
// request size limit: the engine alone (rawfg, prebuilt request line), the reference wrapper
// (map → JSON → C → JSON → map → checkReply) and a host step (wrapper + the snapshot written to
// SQLite). Also: what happens to a user whose snapshot crosses the request limit.

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"hostgo/rawfg"
	"hostgo/refwrap"
)

func pointsSnapshot(n int, seq int64) map[string]any {
	ps := make([]any, 0, n)
	for i := 0; i < n; i++ {
		ps = append(ps, map[string]any{"call": "award", "token": json.Number(fmt.Sprint(seq - int64(i)))})
	}
	s := map[string]any{"at": "home", "seq": json.Number(fmt.Sprint(seq))}
	if n > 0 {
		s["pending"] = ps
	}
	return s
}

func lineOf(req map[string]any) []byte {
	b, err := json.Marshal(req)
	if err != nil {
		panic(err)
	}
	return b
}

type latStats struct {
	n             int
	p50, p99, max time.Duration
}

func measure(reps int, f func()) latStats {
	d := make([]time.Duration, reps)
	for i := 0; i < reps; i++ {
		t := time.Now()
		f()
		d[i] = time.Since(t)
	}
	sort.Slice(d, func(i, j int) bool { return d[i] < d[j] })
	return latStats{n: reps, p50: d[reps/2], p99: d[reps*99/100], max: d[reps-1]}
}

func benchMain(args []string) {
	doc := docText("points@1")
	h, err := rawfg.Load(doc)
	if err != nil {
		panic(err)
	}
	bot, err := refwrap.Load(string(doc))
	if err != nil {
		panic(err)
	}
	dbPath := "/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/bench.db"
	st, err := openStore(dbPath, "FULL", true)
	if err != nil {
		panic(err)
	}
	defer st.Close()
	st.w.Exec(`INSERT INTO users(user, bot, snap, ver, hwm) VALUES('u', 'points', '{}', 0, 0)`)
	sizes := []int{0, 1000, 4000, 8000, 16000, 24000}
	fmt.Println("# step latency with N every-waits (points.bot.json), single goroutine")
	fmt.Println("N\treq_bytes\tcase\tengine_p50\tengine_p99\twrapper_p50\twrapper_p99\thost_p50\thost_p99\tns_per_byte_engine")
	for _, n := range sizes {
		seq := int64(1_000_000)
		snap := pointsSnapshot(n, seq)
		cases := []struct {
			name string
			req  map[string]any
		}{
			{"earn(adds wait)", map[string]any{"op": "step", "snapshot": snap, "event": "btn:earn"}},
			{"outcome(oldest)", map[string]any{"op": "step", "snapshot": snap, "event": "call:award:ok", "token": json.Number(fmt.Sprint(seq - int64(n) + 1))}},
			{"outcome(newest)", map[string]any{"op": "step", "snapshot": snap, "event": "call:award:ok", "token": json.Number(fmt.Sprint(seq))}},
			{"ignored event", map[string]any{"op": "step", "snapshot": snap, "event": "btn:nothing"}},
		}
		reps := 400
		if n >= 8000 {
			reps = 100
		}
		for _, c := range cases {
			req := map[string]any{"id": 1}
			for k, v := range c.req {
				req[k] = v
			}
			line := lineOf(req)
			// engine only
			var out []byte
			eng := measure(reps, func() { out = rawfg.Request(h, line) })
			if bytes.Contains(out, []byte(`"error"`)) {
				fmt.Printf("%d\t%d\t%s\tERROR %s\n", n, len(line), c.name, out[:min(len(out), 200)])
				continue
			}
			// the reference wrapper: the snapshot travels as a map (as a host that decoded it)
			wr := measure(reps, func() {
				_, variant, err := bot.Request(c.req)
				if err != nil || variant != "result" {
					panic(fmt.Sprint(variant, err))
				}
			})
			// a host step: the stored snapshot is passed as raw JSON, the new one is written to SQLite
			raw := json.RawMessage(mustJSON(snap))
			hreq := map[string]any{}
			for k, v := range c.req {
				hreq[k] = v
			}
			hreq["snapshot"] = raw
			hs := measure(reps/2, func() {
				r, v, err := bot.Request(hreq)
				if err != nil || v != "result" {
					panic(err)
				}
				s := mustJSON(r["snapshot"])
				if err := st.tx(nil, func(t txLike) error {
					_, err := t.Exec(`UPDATE users SET snap=?, ver=ver+1 WHERE user='u'`, s)
					return err
				}); err != nil {
					panic(err)
				}
			})
			fmt.Printf("%d\t%d\t%s\t%v\t%v\t%v\t%v\t%v\t%v\t%.2f\n", n, len(line), c.name, eng.p50, eng.p99, wr.p50, wr.p99, hs.p50, hs.p99,
				float64(eng.p50.Nanoseconds())/float64(max(len(line), 1)))
		}
	}
	// throughput: G goroutines sharing one machine vs one machine per goroutine
	fmt.Println("# throughput, outcome(newest) with N waits, engine only (rawfg)")
	fmt.Println("N\tgoroutines\tshared_machine_req_per_s\tmachine_per_goroutine_req_per_s")
	for _, n := range []int{0, 4000, 16000} {
		seq := int64(1_000_000)
		line := lineOf(map[string]any{"id": 1, "op": "step", "snapshot": pointsSnapshot(n, seq), "event": "call:award:ok", "token": seq})
		for _, g := range []int{1, 4, 16} {
			run := func(per bool) float64 {
				hs := make([]rawfg.Handle, g)
				for i := range hs {
					hs[i] = h
					if per {
						hs[i], _ = rawfg.Load(doc)
					}
				}
				dur := 1500 * time.Millisecond
				if n == 0 {
					dur = time.Second
				}
				var wg sync.WaitGroup
				var mu sync.Mutex
				total := 0
				t0 := time.Now()
				for i := 0; i < g; i++ {
					wg.Add(1)
					go func(hh rawfg.Handle) {
						defer wg.Done()
						c := 0
						for time.Since(t0) < dur {
							rawfg.Request(hh, line)
							c++
						}
						mu.Lock()
						total += c
						mu.Unlock()
					}(hs[i])
				}
				wg.Wait()
				el := time.Since(t0)
				if per {
					for _, x := range hs {
						rawfg.Unload(x)
					}
				}
				return float64(total) / el.Seconds()
			}
			fmt.Printf("%d\t%d\t%.0f\t%.0f\n", n, g, run(false), run(true))
		}
	}
	nearLimit(h, bot)
	_ = os.Stdout
	_ = runtime.GOMAXPROCS
	_ = sql.ErrNoRows
	_ = strings.Contains
}

// nearLimit: a user presses an `every` button while the external system is slow. Each step adds
// a wait; the snapshot grows. The engine answers a step whose reply snapshot no longer fits the
// request limit; from then on every request with that snapshot — the outcomes that would shrink
// it included — is `too_large`.
func nearLimit(h rawfg.Handle, bot *refwrap.Bot) {
	limit := rawfg.SetMaxRequest(1 << 20)
	rawfg.SetMaxRequest(limit)
	fmt.Printf("# near the request limit (%d bytes)\n", limit)
	// the largest N whose outcome request fits
	seq := int64(9_000_000_000)
	lo, hi := 1000, 60000
	for lo < hi {
		mid := (lo + hi + 1) / 2
		l := lineOf(map[string]any{"id": 1, "op": "step", "snapshot": pointsSnapshot(mid, seq), "event": "btn:earn"})
		if len(l) <= limit {
			lo = mid
		} else {
			hi = mid - 1
		}
	}
	n := lo
	snap := pointsSnapshot(n, seq)
	line := lineOf(map[string]any{"id": 1, "op": "step", "snapshot": snap, "event": "btn:earn"})
	out := rawfg.Request(h, line)
	var r map[string]any
	d := json.NewDecoder(bytes.NewReader(out))
	d.UseNumber()
	d.Decode(&r)
	next := r["snapshot"]
	ps := next.(map[string]any)["pending"].([]any)
	fmt.Printf("N=%d waits: request %d bytes (limit %d) accepted; the reply snapshot has %d waits, %d bytes\n", n, len(line), limit, len(ps), len(mustJSON(next)))
	// now the outcome of the oldest wait with that snapshot
	oldest := ps[len(ps)-1].(map[string]any)["token"]
	oline := lineOf(map[string]any{"id": 2, "op": "step", "snapshot": next, "event": "call:award:ok", "token": oldest})
	oout := rawfg.Request(h, oline)
	fmt.Printf("outcome request with that snapshot: %d bytes → %s\n", len(oline), oout)
	// the same through the reference wrapper
	resp, v, err := bot.Request(map[string]any{"op": "step", "snapshot": next, "event": "call:award:ok", "token": oldest})
	fmt.Printf("reference wrapper: variant=%q err=%v reply=%v\n", v, err, resp)
	// restart (support) with that snapshot is too large as well; start with the high-water mark is not
	rline := lineOf(map[string]any{"id": 3, "op": "restart", "snapshot": next})
	fmt.Printf("restart with that snapshot: %s\n", rawfg.Request(h, rline))
}
