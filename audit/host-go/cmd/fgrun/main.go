// fgrun: corpus runner + external simulator + invariant checker for the Go host.
//
//	fgrun run CORPUS.jsonl -out OUT.json [-broken V] [-ref REFROOT] [-work DIR]
//	fgrun soak ...   (see soak.go)
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func percentiles(xs []float64) map[string]any {
	if len(xs) == 0 {
		return map[string]any{"n": 0}
	}
	s := append([]float64{}, xs...)
	sort.Float64s(s)
	p := func(q float64) float64 {
		i := int(q * float64(len(s)))
		if i >= len(s) {
			i = len(s) - 1
		}
		return round3(s[i])
	}
	return map[string]any{"n": len(s), "p50": p(0.5), "p95": p(0.95), "p99": p(0.99), "max": round3(s[len(s)-1])}
}

func round3(x float64) float64 { return float64(int64(x*1000+0.5)) / 1000 }

func defaultBin() string {
	exe, _ := os.Executable()
	return filepath.Join(filepath.Dir(exe), "fghost")
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: fgrun run|soak|reload ...")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "run":
		os.Exit(cmdRun(os.Args[2:]))
	case "soak":
		os.Exit(cmdSoak(os.Args[2:]))
	default:
		fmt.Fprintln(os.Stderr, "unknown command", os.Args[1])
		os.Exit(2)
	}
}

func cmdRun(args []string) int {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	out := fs.String("out", "", "output JSON")
	work := fs.String("work", "", "work dir (default $HOSTGO_WORK/<variant>-<name>)")
	ref := fs.String("ref", "", "reference root (cached clean/pristine runs of the correct host)")
	noRef := fs.Bool("no-ref", false, "skip the reference runs")
	o := Opts{}
	fs.StringVar(&o.Bin, "bin", defaultBin(), "fghost binary")
	fs.StringVar(&o.Bots, "bots", "/home/user/figram-audit/hosts/shared/bots", "bots dir")
	fs.StringVar(&o.Broken, "broken", "", "broken variant(s) of the host")
	fs.IntVar(&o.Window, "window", 32, "pipelined deliveries")
	fs.IntVar(&o.Executors, "executors", 4, "outbox executors")
	fs.IntVar(&o.Slots, "cgo-slots", 4, "concurrent C calls")
	fs.StringVar(&o.Sync, "sync", "FULL", "PRAGMA synchronous")
	fs.IntVar(&o.BusyMs, "busy-ms", 3000, "SQLite busy timeout of the host")
	fs.IntVar(&o.MaxNacks, "max-nacks", 150, "give up an update after N NACKs")
	fs.Int64Var(&o.Seed, "seed", 1, "simulator seed")
	fs.BoolVar(&o.Quiet, "quiet", false, "less logging")
	extra := fs.String("host-args", "", "extra host args (space separated)")
	fs.Parse(args)
	var pos []string
	for fs.NArg() > 0 { // allow flags after the positional argument
		pos = append(pos, fs.Arg(0))
		fs.Parse(fs.Args()[1:])
	}
	if len(pos) != 1 || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: fgrun run CORPUS -out OUT.json")
		return 2
	}
	if *extra != "" {
		o.ExtraArgs = strings.Fields(*extra)
	}
	corpus := pos[0]
	items, err := loadCorpus(corpus)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	name := strings.TrimSuffix(filepath.Base(corpus), ".jsonl")
	variant := o.Broken
	if variant == "" {
		variant = "ok"
	}
	if *work == "" {
		base := os.Getenv("HOSTGO_WORK")
		if base == "" {
			base = "/tmp/hostgo-work"
		}
		*work = filepath.Join(base, strings.ReplaceAll(variant, ",", "+")+"-"+name)
	}
	os.RemoveAll(*work)
	os.MkdirAll(*work, 0o755)
	r := NewRunner(items, filepath.Join(*work, "run"), o)
	if err := r.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "run failed:", err)
		return 3
	}
	before := dumpForRestart(r.db, r.ext)
	if err := r.RestartCheck(); err != nil {
		fmt.Fprintln(os.Stderr, "restart check failed:", err)
	}
	after := dumpForRestart(r.db, r.ext)
	st, err := loadState(r.db)
	if err != nil {
		fmt.Fprintln(os.Stderr, "load state:", err)
		return 3
	}
	scen, _ := items[0].Raw["scenario"].(string)
	seed := items[0].num("seed")
	c := &checkCtx{r: r, st: st, scen: scen, family: r.family, inv: map[string]string{}, fails: map[string][]string{},
		before: before, after: after}
	if !*noRef {
		refRoot := *ref
		if refRoot == "" {
			refRoot = filepath.Join(*work, "ref")
		}
		ro := o
		ro.Broken = ""
		ro.Quiet = true
		clean := stripClean(items)
		pristine := stripPristine(items)
		for _, k := range []struct {
			name  string
			items []Item
			dst   **DBState
		}{{"clean", clean, &c.clean}, {"pristine", pristine, &c.pristine}} {
			if k.name == "pristine" && len(pristine) == len(clean) {
				c.pristine = nil
				continue
			}
			dir := filepath.Join(refRoot, name, k.name)
			dbp := filepath.Join(dir, "host.sqlite")
			if _, err := os.Stat(filepath.Join(dir, "DONE")); err != nil {
				os.RemoveAll(dir)
				rr := NewRunner(k.items, dir, ro)
				if err := rr.Run(); err != nil {
					fmt.Fprintln(os.Stderr, "reference run failed:", err)
					return 3
				}
				os.WriteFile(filepath.Join(dir, "DONE"), []byte("ok\n"), 0o644)
			}
			s, err := loadState(dbp)
			if err != nil {
				fmt.Fprintln(os.Stderr, "reference state:", err)
				return 3
			}
			*k.dst = s
		}
	}
	c.run()
	res := buildOutput(r, st, c, scen, seed, o, items)
	b, _ := json.MarshalIndent(res, "", " ")
	os.MkdirAll(filepath.Dir(*out), 0o755)
	if err := os.WriteFile(*out, b, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	var fails []string
	for _, k := range sortedKeys(c.inv) {
		if c.inv[k] != "PASS" {
			fails = append(fails, k)
		}
	}
	sum, _ := json.Marshal(map[string]any{"scenario": name, "broken": o.Broken, "elapsed_s": round3(r.Elapsed.Seconds()),
		"fails": fails, "counts": res["counts"], "latency": res["latency"]})
	fmt.Println(string(sum))
	if len(fails) > 0 {
		return 1
	}
	return 0
}

func buildOutput(r *Runner, st *DBState, c *checkCtx, scen string, seed int64, o Opts, items []Item) map[string]any {
	users := map[string]any{}
	by := changedByUser(st)
	recovered, repairs, resets := 0, 0, 0
	for _, u := range sortedKeys(st.Users) {
		ts := by[u]
		if ts == nil {
			ts = []Trans{}
		}
		for _, t := range ts {
			if t.Recovered {
				recovered++
			}
			if t.Kind == "repair" {
				repairs++
			}
			if t.Kind == "reset" {
				resets++
			}
		}
		users[u] = map[string]any{"snapshot": st.Users[u]["snapshot"], "doc": st.Users[u]["doc"], "transitions": ts}
	}
	var effects []map[string]any
	r.ext.mu.Lock()
	for _, k := range sortedKeys(r.ext.Effects) {
		e := r.ext.Effects[k]
		effects = append(effects, map[string]any{"key": k, "user": e.User, "call": e.Op})
	}
	seen := map[string]bool{}
	for _, d := range r.ext.TG {
		if seen[d.Key] {
			continue
		}
		seen[d.Key] = true
		effects = append(effects, map[string]any{"key": d.Key, "user": d.User, "send": d.Text})
	}
	r.ext.mu.Unlock()
	sort.SliceStable(effects, func(i, j int) bool {
		ki, kj := effects[i]["key"].(string), effects[j]["key"].(string)
		if ki != kj {
			return ki < kj
		}
		_, ci := effects[i]["call"]
		return !ci
	})
	acks := map[string]int{}
	var e2e, step, lat []float64
	for _, a := range r.Acks {
		acks[a.Status]++
		e2e = append(e2e, float64(a.E2EUs)/1000)
		if !a.Raw && a.Status != "rejected" {
			step = append(step, float64(a.StepUs)/1000)
		}
		lat = append(lat, float64(a.LatUs)/1000)
	}
	outboxN := len(st.Outbox)
	timersBy := map[string]int{}
	for _, t := range st.Timers {
		timersBy[fmt.Sprint(t[4])]++
	}
	trN := 0
	for _, ts := range by {
		trN += len(ts)
	}
	var lastStats map[string]any
	if len(r.StopStats) > 0 {
		lastStats = r.StopStats[0]
	}
	counts := map[string]any{
		"users": len(st.Users), "transitions": trN, "steps_logged": len(st.Trans), "updates_acked": len(r.Acks),
		"updates_abandoned": len(r.Abandoned), "acks_by_status": acks, "nacks": r.Nacks.Load(), "nack_reasons": r.NackWhy,
		"crashes": len(r.Fired), "host_restarts": r.Restarts, "recovered": recovered, "repairs": repairs, "resets": resets,
		"rejected": st.Rejected, "outbox_rows": outboxN, "timers_by_state": timersBy, "host_log": st.Log,
		"ext": r.ext.Counts(), "engine_faults": st.FaultLog,
	}
	if lastStats != nil {
		counts["host_stats_final_process"] = lastStats["stats"]
		counts["binding_final_process"] = lastStats["binding"]
	}
	n := float64(len(r.Acks))
	latency := map[string]any{"host_update_ms": percentiles(lat), "host_step_ms": percentiles(step), "e2e_ms": percentiles(e2e),
		"elapsed_s": round3(r.Elapsed.Seconds())}
	if r.Elapsed > 0 {
		latency["throughput_updates_per_s"] = round3(n / r.Elapsed.Seconds())
	}
	var brk any
	if o.Broken != "" {
		brk = o.Broken
	}
	return map[string]any{
		"host": "go", "scenario": scen, "seed": seed, "broken": brk, "bot": r.family,
		"users": users, "effects": effects, "invariants": c.inv, "invariant_failures": c.fails,
		"counts": counts, "latency": latency, "faults_injected": r.Faults, "crash_log": r.Fired, "exits": r.Exits,
		"config": map[string]any{"window": o.Window, "executors": o.Executors, "cgo_slots": o.Slots, "sync": o.Sync,
			"busy_ms": o.BusyMs, "engine": "libfigram.a (cgo, in-process)", "bin": o.Bin},
	}
}
