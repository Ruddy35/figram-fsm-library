package main

// Soak: one host process under continuous load for a fixed time, with periodic
// document reloads (fresh figram_load_doc + retire of the old automaton after its
// in-flight calls) and sampling of the host process: RSS, VmSize, threads, fds
// (from /proc/<pid>), goroutines / Go heap / live automata (from the host).

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

func procSample(pid int) map[string]int64 {
	out := map[string]int64{}
	b, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return out
	}
	for _, l := range strings.Split(string(b), "\n") {
		k, v, ok := strings.Cut(l, ":")
		if !ok {
			continue
		}
		switch k {
		case "VmRSS", "VmSize", "VmHWM", "Threads":
			f := strings.Fields(v)
			if len(f) > 0 {
				n, _ := strconv.ParseInt(f[0], 10, 64)
				out[k] = n
			}
		}
	}
	if ents, err := os.ReadDir(fmt.Sprintf("/proc/%d/fd", pid)); err == nil {
		out["fds"] = int64(len(ents))
	}
	return out
}

func cmdSoak(args []string) int {
	fs := flag.NewFlagSet("soak", flag.ExitOnError)
	secs := fs.Int("secs", 600, "duration")
	users := fs.Int("users", 40, "concurrent virtual users")
	reloadEvery := fs.Duration("reload-every", 5*time.Second, "document reload period (0 = never)")
	sampleEvery := fs.Duration("sample-every", 15*time.Second, "sampling period")
	pause := fs.Duration("think", 5*time.Millisecond, "think time between a user's updates")
	out := fs.String("out", "", "output JSON")
	work := fs.String("work", "", "work dir")
	o := Opts{}
	fs.StringVar(&o.Bin, "bin", defaultBin(), "fghost binary")
	fs.StringVar(&o.Bots, "bots", "/home/user/figram-audit/hosts/shared/bots", "bots dir")
	fs.StringVar(&o.Broken, "broken", "", "broken variant(s)")
	fs.IntVar(&o.Executors, "executors", 4, "executors")
	fs.IntVar(&o.Slots, "cgo-slots", 4, "cgo slots")
	fs.StringVar(&o.Sync, "sync", "NORMAL", "PRAGMA synchronous")
	fs.IntVar(&o.BusyMs, "busy-ms", 3000, "busy timeout")
	fs.Parse(args)
	o.Window, o.MaxNacks, o.Seed, o.Quiet = *users, 150, 1, true
	if *work == "" {
		*work = filepath.Join(os.TempDir(), "hostgo-soak")
	}
	os.RemoveAll(*work)
	initItem := Item{Raw: map[string]any{"ctl": "init", "bot": "pizza", "scenario": "soak", "seed": float64(1)}}
	r := NewRunner([]Item{initItem}, *work, o)
	os.MkdirAll(r.work, 0o755)
	var err error
	r.hostStderr, err = os.OpenFile(filepath.Join(r.work, "host.stderr"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	r.ext = NewExtSim(1)
	if err := r.ext.Serve(r.extSock); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	if err := r.startHost(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	script := []struct{ ev, arg string }{{"cmd:start", ""}, {"btn:order", ""}, {"btn:pizza", ""}, {"btn:size", "L"},
		{"btn:add", ""}, {"btn:cart", ""}, {"btn:checkout", ""}, {"msg:text", "Lenina 5"}, {"payment:success", ""}}
	deadline := time.Now().Add(time.Duration(*secs) * time.Second)
	var uidMu sync.Mutex
	nextUID := int64(1)
	var samples []map[string]any
	var smu sync.Mutex
	t0 := time.Now()
	var reloads, reloadErrs int
	var reloadMs []float64
	stop := make(chan struct{})
	if *reloadEvery > 0 {
		go func() {
			tk := time.NewTicker(*reloadEvery)
			defer tk.Stop()
			for {
				select {
				case <-stop:
					return
				case <-tk.C:
					res, err := r.postJSON(r.ctlCl, "/ctl", map[string]any{"ctl": "reload_doc", "doc": "pizza"})
					smu.Lock()
					if err != nil {
						reloadErrs++
					} else {
						reloads++
						if v, ok := res["load_ms"].(float64); ok {
							reloadMs = append(reloadMs, v)
						}
					}
					smu.Unlock()
				}
			}
		}()
	}
	sample := func() {
		r.mu.Lock()
		p := r.cur
		acked := len(r.Acks)
		r.mu.Unlock()
		if p == nil {
			return
		}
		ps := procSample(p.pid)
		code, rb, _ := r.post(r.client, "/stats", nil)
		s := map[string]any{"t_s": round3(time.Since(t0).Seconds()), "pid": p.pid, "proc": ps, "acked": acked}
		if code == 200 {
			var st map[string]any
			json.Unmarshal(rb, &st)
			s["goroutines"] = st["goroutines"]
			s["go_heap_alloc"] = st["go_heap_alloc"]
			s["binding"] = st["binding"]
			s["retired"] = st["retired"]
		}
		smu.Lock()
		samples = append(samples, s)
		smu.Unlock()
		b, _ := json.Marshal(s)
		fmt.Fprintln(os.Stderr, string(b))
	}
	go func() {
		tk := time.NewTicker(*sampleEvery)
		defer tk.Stop()
		sample()
		for {
			select {
			case <-stop:
				return
			case <-tk.C:
				sample()
			}
		}
	}()
	var wg, dwg sync.WaitGroup
	for v := 0; v < *users; v++ {
		wg.Add(1)
		go func(v int) {
			defer wg.Done()
			for iter := 0; time.Now().Before(deadline); iter++ {
				user := fmt.Sprintf("s%03d_%d", v, iter)
				for _, s := range script {
					uidMu.Lock()
					uid := nextUID
					nextUID++
					uidMu.Unlock()
					body := map[string]any{"user": user, "update_id": uid, "event": s.ev}
					if s.arg != "" {
						body["arg"] = s.arg
					}
					bb, _ := json.Marshal(body)
					r.mu.Lock()
					r.busy[user]++
					r.mu.Unlock()
					r.inflight.Add(1)
					dwg.Add(1)
					r.deliver(&delivery{user: user, uid: fmt.Sprintf("tg:%d", uid), body: bb, start: time.Now()}, &dwg)
					time.Sleep(*pause)
				}
			}
		}(v)
	}
	wg.Wait()
	dwg.Wait()
	close(stop)
	sample()
	r.Elapsed = time.Since(t0)
	if _, err := r.ctl(map[string]any{"ctl": "barrier"}); err != nil {
		fmt.Fprintln(os.Stderr, "barrier:", err)
	}
	st, err := r.stopHost()
	if err == nil {
		r.StopStats = append(r.StopStats, st)
	}
	r.ext.Close()
	dbs, err := loadState(r.db)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	c := &checkCtx{r: r, st: dbs, scen: "soak", family: "pizza", inv: map[string]string{}, fails: map[string][]string{}}
	c.run()
	drift := map[string]any{}
	smu.Lock()
	n := len(samples)
	if n >= 6 {
		avg := func(from, to int, get func(map[string]any) float64) float64 {
			s := 0.0
			for _, x := range samples[from:to] {
				s += get(x)
			}
			return s / float64(to-from)
		}
		for _, k := range []string{"VmRSS", "VmSize", "Threads", "fds"} {
			key := k
			get := func(m map[string]any) float64 { return float64(m["proc"].(map[string]int64)[key]) }
			a, b := avg(1, 1+n/3, get), avg(n-1-n/3, n-1, get)
			drift[k] = map[string]any{"early": round3(a), "late": round3(b), "ratio": round3(b / a)}
		}
		get := func(m map[string]any) float64 { v, _ := m["goroutines"].(float64); return v }
		a, b := avg(1, 1+n/3, get), avg(n-1-n/3, n-1, get)
		drift["goroutines"] = map[string]any{"early": round3(a), "late": round3(b), "ratio": round3(b / a)}
	}
	smu.Unlock()
	var lat []float64
	for _, a := range r.Acks {
		lat = append(lat, float64(a.E2EUs)/1000)
	}
	res := map[string]any{"host": "go", "scenario": "soak", "broken": o.Broken, "secs": *secs, "users": *users,
		"updates_acked": len(r.Acks), "elapsed_s": round3(r.Elapsed.Seconds()),
		"throughput_updates_per_s": round3(float64(len(r.Acks)) / r.Elapsed.Seconds()),
		"e2e_ms": percentiles(lat), "reloads": reloads, "reload_errors": reloadErrs, "reload_load_ms": percentiles(reloadMs),
		"samples": samples, "drift": drift, "invariants": c.inv, "invariant_failures": c.fails, "exits": r.Exits,
		"final_stats": st}
	b, _ := json.MarshalIndent(res, "", " ")
	if *out != "" {
		os.WriteFile(*out, b, 0o644)
	}
	var fails []string
	for _, k := range sortedKeys(c.inv) {
		if c.inv[k] != "PASS" {
			fails = append(fails, k)
		}
	}
	sum, _ := json.Marshal(map[string]any{"updates": len(r.Acks), "elapsed_s": round3(r.Elapsed.Seconds()), "reloads": reloads,
		"reload_errors": reloadErrs, "drift": drift, "fails": fails, "e2e_ms": res["e2e_ms"]})
	fmt.Println(string(sum))
	if len(fails) > 0 || reloadErrs > 0 {
		return 1
	}
	return 0
}
