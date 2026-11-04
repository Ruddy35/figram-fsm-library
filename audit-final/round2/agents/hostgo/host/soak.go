package main

// Memory soak of the embedded engine under a Go host workload: goroutines keep user snapshots in
// memory, press an `every` button (pending grows to thousands of waits), then answer the waits
// (pending shrinks), on several documents; machines are reloaded (load new, swap, Close old while
// other goroutines may hold it) every few hundred milliseconds. Every reply is validated by the
// reference wrapper; the snapshot sequence of each goroutine is checked against its own model.
// RSS, the Go heap and glibc malloc are sampled; the trend after warm-up decides.

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"runtime"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"hostgo/rawfg"
)

func rssKBNow() int64 {
	b, err := os.ReadFile("/proc/self/statm")
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b))
	p, _ := strconv.ParseInt(f[1], 10, 64)
	return p * 4
}

type memSample struct {
	T          float64 `json:"t_s"`
	RSSKB      int64   `json:"rss_kb"`
	GoHeapKB   int64   `json:"go_heap_inuse_kb"`
	GoSysKB    int64   `json:"go_sys_kb"`
	MallocKB   int64   `json:"malloc_inuse_kb"`
	Goroutines int     `json:"goroutines"`
	Steps      int64   `json:"steps"`
	Reloads    int64   `json:"reloads"`
	MaxPending int64   `json:"max_pending"`
	SumPending int64   `json:"sum_pending"`
}

func soakMain(args []string) {
	d := 10 * time.Minute
	if len(args) > 0 {
		if x, err := time.ParseDuration(args[0]); err == nil {
			d = x
		}
	}
	workers := 48
	peak := 4000
	reloadEvery := 300 * time.Millisecond
	out := "/home/user/figram-audit/agents6/hostgo/soak.json"
	if len(args) > 1 {
		out = args[1]
	}
	reg := &Registry{}
	for _, b := range []string{"points", "shop", "booking"} {
		dv, err := loadDoc(b + "@1")
		if err != nil {
			panic(err)
		}
		reg.ptr(b).Store(dv)
	}
	var steps, violations, maxPend, closedR atomic.Int64
	var sumPend atomic.Int64
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			rng := rand.New(rand.NewSource(int64(w)))
			// points users: grow to a random peak then drain; others: simple loops
			bot := []string{"points", "points", "shop", "booking"}[w%4]
			var snap map[string]any
			var waits []int64 // tokens of award waits (newest last)
			target := rng.Intn(peak) + 1
			growing := true
			call := func(req map[string]any) map[string]any {
				rep, err := reg.call(bot, req, func(n []string) map[string]bool {
					g := map[string]bool{}
					for _, x := range n {
						g[x] = rng.Intn(2) == 0
					}
					return g
				})
				steps.Add(1)
				if err != nil || rep.ErrCode != "" {
					violations.Add(1)
					fmt.Fprintln(os.Stderr, "soak:", err, rep.ErrCode, rep.ErrText)
					return nil
				}
				return rep.Snapshot
			}
			snap = call(map[string]any{"op": "start"})
			for {
				select {
				case <-stop:
					return
				default:
				}
				if snap == nil {
					return
				}
				var req map[string]any
				switch bot {
				case "points":
					if growing && len(waits) < target {
						req = map[string]any{"op": "step", "snapshot": snap, "event": "btn:earn"}
					} else if len(waits) > 0 {
						growing = false
						i := rng.Intn(len(waits))
						tok := waits[i]
						waits = append(waits[:i], waits[i+1:]...)
						req = map[string]any{"op": "step", "snapshot": snap, "event": "call:award:ok", "token": tok}
					} else {
						growing = true
						target = rng.Intn(peak) + 1
						req = map[string]any{"op": "restart", "snapshot": snap}
					}
				default:
					evs := map[string][]string{
						"shop":    {"btn:order", "btn:add", "btn:checkout", "msg:text", "payment:success", "btn:cancel", "cmd:start", "btn:more", "btn:cart"},
						"booking": {"btn:book", "btn:slot", "btn:again", "btn:leave", "btn:ping", "cmd:start", "btn:topup", "btn:pay"},
					}[bot]
					req = map[string]any{"op": "step", "snapshot": snap, "event": evs[rng.Intn(len(evs))]}
				}
				ns := call(req)
				if ns == nil {
					return
				}
				if bot == "points" {
					// model: an earn adds the step's token; an outcome removes its wait
					if req["event"] == "btn:earn" {
						waits = append(waits, numOf(ns["seq"]))
					}
					got := len(pendingOf(ns))
					if got != len(waits) {
						violations.Add(1)
						fmt.Fprintf(os.Stderr, "soak: pending %d, model %d\n", got, len(waits))
						return
					}
					sumPend.Add(int64(got) - int64(len(pendingOf(snap))))
					for {
						m := maxPend.Load()
						if int64(got) <= m || maxPend.CompareAndSwap(m, int64(got)) {
							break
						}
					}
				} else if len(pendingOf(ns)) > 0 { // shop/booking: answer waits at once
					for wt := range pendingOf(ns) {
						outcome := map[string]string{"create_order": "created", "reserve": "held", "notify": "sent", "log_ping": "ok"}[wt.a]
						if outcome == "" {
							outcome = "failed"
						}
						if x := call(map[string]any{"op": "step", "snapshot": ns, "event": "call:" + wt.a + ":" + outcome, "token": wt.t}); x != nil {
							ns = x
						}
						break
					}
				}
				snap = ns
				// the stored form: JSON text, decoded again with json.Number (as a host reading its store)
				snap = decodeJSON(mustJSON(snap)).(map[string]any)
			}
		}(w)
	}
	// reloader
	var reloads atomic.Int64
	wg.Add(1)
	go func() {
		defer wg.Done()
		r := rand.New(rand.NewSource(99))
		for {
			select {
			case <-stop:
				return
			case <-time.After(reloadEvery):
			}
			b := []string{"points", "shop", "booking"}[r.Intn(3)]
			cur := reg.Current(b)
			next := b + "@2"
			if strings.HasSuffix(cur.key, "@2") {
				next = b + "@1"
			}
			if err := reg.Install(nil, next); err != nil {
				panic(err)
			}
			reloads.Add(1)
		}
	}()
	var samples []memSample
	t0 := time.Now()
	sample := func() {
		var ms runtime.MemStats
		runtime.ReadMemStats(&ms)
		samples = append(samples, memSample{T: time.Since(t0).Seconds(), RSSKB: rssKBNow(), GoHeapKB: int64(ms.HeapInuse / 1024),
			GoSysKB: int64(ms.Sys / 1024), MallocKB: int64(rawfg.MallocInUse() / 1024), Goroutines: runtime.NumGoroutine(),
			Steps: steps.Load(), Reloads: reloads.Load(), MaxPending: maxPend.Load(), SumPending: sumPend.Load()})
		s := samples[len(samples)-1]
		fmt.Printf("t=%5.0fs rss=%7d KB goheap=%7d KB gosys=%7d KB malloc=%7d KB steps=%9d reloads=%5d pending(sum)=%7d max=%d\n",
			s.T, s.RSSKB, s.GoHeapKB, s.GoSysKB, s.MallocKB, s.Steps, s.Reloads, s.SumPending, s.MaxPending)
	}
	sample()
	for time.Since(t0) < d {
		time.Sleep(15 * time.Second)
		sample()
	}
	close(stop)
	wg.Wait()
	closedR.Store(reg.closed.Load())
	reg.CloseAll()
	runtime.GC()
	debug.FreeOSMemory()
	sample()
	// trend: compare the median RSS of the second third with the last third
	n := len(samples)
	med := func(a []memSample, f func(memSample) int64) int64 {
		v := []int64{}
		for _, s := range a {
			v = append(v, f(s))
		}
		sort.Slice(v, func(i, j int) bool { return v[i] < v[j] })
		return v[len(v)/2]
	}
	mid := samples[n/3 : 2*n/3]
	last := samples[2*n/3 : n-1]
	rssMid, rssLast := med(mid, func(s memSample) int64 { return s.RSSKB }), med(last, func(s memSample) int64 { return s.RSSKB })
	mMid, mLast := med(mid, func(s memSample) int64 { return s.MallocKB }), med(last, func(s memSample) int64 { return s.MallocKB })
	res := map[string]any{"samples": samples, "steps": steps.Load(), "reloads": reloads.Load(), "violations": violations.Load(),
		"closed_retries": closedR.Load(), "rss_mid_kb": rssMid, "rss_last_kb": rssLast, "malloc_mid_kb": mMid, "malloc_last_kb": mLast}
	b, _ := json.MarshalIndent(res, "", " ")
	os.WriteFile(out, b, 0o644)
	fmt.Printf("steps %d, reloads %d, closed-retries %d, violations %d; RSS median %d KB (middle third) → %d KB (last third); malloc %d → %d KB\n",
		steps.Load(), reloads.Load(), closedR.Load(), violations.Load(), rssMid, rssLast, mMid, mLast)
	if violations.Load() > 0 || rssLast > rssMid+65536 {
		fmt.Println("SOAK: FAIL")
		os.Exit(1)
	}
	fmt.Println("SOAK: PASS")
}
