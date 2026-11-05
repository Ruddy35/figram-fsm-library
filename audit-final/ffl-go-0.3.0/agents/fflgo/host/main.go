// audithost: a Go host on ffl-go's public API, compared step by step with figram-engine.
//
//	audithost [-workers N] [-rounds R] [-reload] [-negctl MODE] traces/index.tsv
//
// For every scenario of `figram trace` (one user per scenario, many goroutines, one Bot per
// document shared by all of them) the host drives the typed API: Start/StartWith with Decide,
// Step, Timer and Outcome with Decide, plus side requests (repeated, stale, zero-token and
// "failed" outcomes, an outcome for an action without outcomes, a stale timer, Restart, a
// recovered step). Two comparisons per call:
//
//   - same request: the JSON request ffl-go sends (the same fields from the same typed values,
//     another id) is sent to a figram-engine process; its reply must equal ffl-go's typed result
//     (as JSON values: objects unordered, arrays ordered). Any difference is a bug.
//   - engine chain: the same scenario is replayed with the engine's own snapshot text passed on
//     unchanged; ffl-go's result must equal the engine's. A difference here and none in the
//     first comparison comes from ffl-go's re-encoding of the snapshot.
//
// -reload swaps every document's Bot (Load, atomic switch, Close) while requests run: a call
// that gets ErrClosed repeats its whole step, Decide included, on the new Bot.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"os"
	"os/exec"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	figram "github.com/Ruddy35/ffl-go"
)

const engineBin = "/home/user/figram-audit-worktrees/cleanup-int/.lake/build/bin/figram-engine"

// ---------------------------------------------------------------- engine processes

type proc struct {
	mu  sync.Mutex
	cmd *exec.Cmd
	w   io.WriteCloser
	r   *bufio.Reader
	id  int64
}

func startProc(doc string, extra ...string) *proc {
	cmd := exec.Command(engineBin, append(extra, doc)...)
	w, _ := cmd.StdinPipe()
	out, _ := cmd.StdoutPipe()
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		panic(err)
	}
	return &proc{cmd: cmd, w: w, r: bufio.NewReaderSize(out, 1<<20)}
}

// do sends req (without id) and returns the reply line without its id field, as raw JSON.
func (p *proc) do(req map[string]any) []byte {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.id++
	m := map[string]any{"id": p.id}
	for k, v := range req {
		m[k] = v
	}
	line, err := json.Marshal(m)
	if err != nil {
		panic(err)
	}
	if _, err := p.w.Write(append(line, '\n')); err != nil {
		panic(err)
	}
	out, err := p.r.ReadBytes('\n')
	if err != nil {
		panic(err)
	}
	prefix := fmt.Sprintf(`{"id":%d,`, p.id)
	if !bytes.HasPrefix(out, []byte(prefix)) {
		panic("engine reply without the id: " + string(out))
	}
	return append([]byte("{"), bytes.TrimSpace(out[len(prefix):])...)
}

type pool struct {
	ps []*proc
	n  atomic.Int64
}

func (p *pool) do(req map[string]any) []byte {
	return p.ps[int(p.n.Add(1))%len(p.ps)].do(req)
}

// ---------------------------------------------------------------- comparison

func value(b []byte) any {
	var v any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		return "INVALID JSON: " + string(b)
	}
	return v
}

// fflJSON is the typed result of ffl-go as the engine's reply object.
func fflJSON(res *figram.Result, err error) []byte {
	var pe *figram.ProtocolError
	switch {
	case errors.As(err, &pe):
		b, _ := json.Marshal(map[string]string{"error": pe.Message, "code": pe.Code})
		return b
	case err != nil:
		b, _ := json.Marshal(map[string]string{"go_error": err.Error()})
		return b
	}
	b, e := json.Marshal(res)
	if e != nil {
		panic(e)
	}
	return b
}

type stats struct {
	mu       sync.Mutex
	counts   map[string]int
	examples map[string]string
}

func (s *stats) add(k string, ex string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.counts[k]++
	if ex != "" && s.examples[k] == "" {
		s.examples[k] = ex
	}
}

var st = &stats{counts: map[string]int{}, examples: map[string]string{}}
var progress atomic.Int64
var negctl string

// classify a chain difference: only the order of consecutive cancel_timer actions differs.
func cancelOrderOnly(a, b any) bool {
	am, ok1 := a.(map[string]any)
	bm, ok2 := b.(map[string]any)
	if !ok1 || !ok2 || !reflect.DeepEqual(am["snapshot"], bm["snapshot"]) || !reflect.DeepEqual(am["recovered"], bm["recovered"]) {
		return false
	}
	aa, _ := am["actions"].([]any)
	ba, _ := bm["actions"].([]any)
	if len(aa) != len(ba) {
		return false
	}
	norm := func(l []any) []string {
		out := make([]string, len(l))
		for i, x := range l {
			b, _ := json.Marshal(x)
			out[i] = string(b)
		}
		// sort each run of cancel_timer actions
		for i := 0; i < len(out); {
			j := i
			for j < len(out) && strings.HasPrefix(out[j], `{"cancel_timer"`) {
				j++
			}
			if j > i {
				sort.Strings(out[i:j])
				i = j
			} else {
				i++
			}
		}
		return out
	}
	return reflect.DeepEqual(norm(aa), norm(ba))
}

// ---------------------------------------------------------------- bots and reload

type botv struct {
	b   *figram.Bot
	gen int
}

type docw struct {
	path  string
	text  string
	cur   atomic.Pointer[botv]
	eng   *pool
	gen   atomic.Int64
	loads atomic.Int64
}

func (d *docw) reload() {
	b, err := figram.Load(d.text)
	if err != nil {
		panic(err)
	}
	old := d.cur.Swap(&botv{b: b, gen: int(d.gen.Add(1))})
	d.loads.Add(1)
	if old != nil {
		old.b.Close()
	}
}

var retries atomic.Int64

// call runs one typed operation through Decide (for need), comparing every underlying call with
// the engine given the same request; on ErrClosed it repeats the whole step on the current Bot.
func (d *docw) call(name string, eval map[string]bool, op func(b *figram.Bot, g map[string]bool) (*figram.Result, error),
	req func(g map[string]bool) map[string]any) (*figram.Result, error) {
	r, e, _ := d.callG(name, eval, op, req)
	return r, e
}

// callG is call that also returns the guards of the last underlying call (Decide's final guards).
func (d *docw) callG(name string, eval map[string]bool, op func(b *figram.Bot, g map[string]bool) (*figram.Result, error),
	req func(g map[string]bool) map[string]any) (*figram.Result, error, map[string]bool) {
	for attempt := 0; ; attempt++ {
		bv := d.cur.Load()
		final := map[string]bool{}
		res, err := figram.Decide(nil, func(c string) (bool, error) { return eval[c], nil },
			func(g map[string]bool) (*figram.Result, error) {
				final = map[string]bool{}
				for k, v := range g {
					final[k] = v
				}
				r, e := op(bv.b, g)
				if errors.Is(e, figram.ErrClosed) {
					return r, e
				}
				got := fflJSON(r, e)
				if negctl == "flip-seq" && r != nil && len(r.Need) == 0 {
					r2 := *r
					r2.Snapshot.Seq++
					got = fflJSON(&r2, e)
				}
				want := d.eng.do(req(g))
				if reflect.DeepEqual(value(got), value(want)) {
					st.add("same-request:equal", "")
				} else {
					rq, _ := json.Marshal(req(g))
					st.add("same-request:DIFF:"+name, fmt.Sprintf("request %s\n      ffl-go %s\n      engine %s", rq, got, want))
				}
				return r, e
			})
		if errors.Is(err, figram.ErrClosed) {
			retries.Add(1)
			if attempt > 1000 {
				panic("ErrClosed forever")
			}
			continue
		}
		progress.Add(1)
		return res, err, final
	}
}

func withGuards(m map[string]any, g map[string]bool) map[string]any {
	if len(g) > 0 {
		m["guards"] = g
	}
	return m
}

// ---------------------------------------------------------------- scenarios

type tstep struct {
	Event    string          `json:"event"`
	Token    *uint64         `json:"token"`
	Guards   map[string]bool `json:"guards"`
	Snapshot json.RawMessage `json:"snapshot"`
	Actions  json.RawMessage `json:"actions"`
}

type trace struct {
	Start *struct {
		Event    *string         `json:"event"`
		Guards   map[string]bool `json:"guards"`
		Snapshot json.RawMessage `json:"snapshot"`
	} `json:"start"`
	From  json.RawMessage `json:"from"`
	Steps []tstep         `json:"steps"`
}

type job struct {
	d *docw
	t trace
	n int
}

// engine-chain reply → raw snapshot
func rawSnapshot(reply []byte) json.RawMessage {
	var r struct {
		Snapshot json.RawMessage `json:"snapshot"`
	}
	json.Unmarshal(reply, &r)
	return r.Snapshot
}

func chainCompare(kind string, res *figram.Result, err error, want []byte) {
	got := fflJSON(res, err)
	a, b := value(got), value(want)
	switch {
	case reflect.DeepEqual(a, b):
		st.add("chain:equal", "")
	case cancelOrderOnly(a, b):
		st.add("chain:cancel_timer-order:"+kind, fmt.Sprintf("ffl-go %s\n      engine %s", got, want))
	default:
		st.add("chain:DIFF:"+kind, fmt.Sprintf("ffl-go %s\n      engine %s", got, want))
	}
}

func gone(raw json.RawMessage) json.RawMessage {
	// replace the value of "at" (the first "at" key of the snapshot object) by a removed message
	i := bytes.Index(raw, []byte(`"at":"`))
	j := bytes.IndexByte(raw[i+6:], '"')
	return append(append(append([]byte{}, raw[:i+6]...), "__gone__"...), raw[i+6+j:]...)
}

func runScenario(j job, rng *rand.Rand) {
	d, t := j.d, j.t
	var snap figram.Snapshot
	var snapE json.RawMessage
	if t.Start != nil {
		var res *figram.Result
		var err error
		ereq := map[string]any{"op": "start"}
		if t.Start.Event != nil {
			ev := *t.Start.Event
			res, err = d.call("startwith", t.Start.Guards,
				func(b *figram.Bot, g map[string]bool) (*figram.Result, error) { return b.StartWith(0, ev, g) },
				func(g map[string]bool) map[string]any { return withGuards(map[string]any{"op": "start", "event": ev}, g) })
			ereq = withGuards(map[string]any{"op": "start", "event": ev}, t.Start.Guards)
		} else {
			res, err = d.call("start", nil,
				func(b *figram.Bot, g map[string]bool) (*figram.Result, error) { return b.Start(0) },
				func(g map[string]bool) map[string]any { return map[string]any{"op": "start"} })
		}
		want := d.eng.do(ereq)
		chainCompare("start", res, err, want)
		if err != nil {
			st.add("scenario:start-error", err.Error())
			return
		}
		snap, snapE = res.Snapshot, rawSnapshot(want)
	} else {
		if err := json.Unmarshal(t.From, &snap); err != nil {
			panic(err)
		}
		snapE = t.From
	}
	for i, s := range t.Steps {
		ev, g := s.Event, s.Guards
		cur := snap
		var res *figram.Result
		var err error
		kind := "step"
		ereq := withGuards(map[string]any{"op": "step", "snapshot": snapE, "event": ev}, g)
		switch {
		case s.Token != nil && strings.HasPrefix(ev, "call:"):
			kind = "outcome"
			k := strings.LastIndexByte(ev, ':')
			action, outcome, tok := ev[len("call:"):k], ev[k+1:], *s.Token
			out := func(sn figram.Snapshot, a, o string, tk uint64) func(b *figram.Bot, g map[string]bool) (*figram.Result, error) {
				return func(b *figram.Bot, g map[string]bool) (*figram.Result, error) { return b.Outcome(sn, a, o, tk, g) }
			}
			oreq := func(sn figram.Snapshot, a, o string, tk uint64) func(g map[string]bool) map[string]any {
				return func(g map[string]bool) map[string]any {
					return withGuards(map[string]any{"op": "step", "snapshot": sn, "event": "call:" + a + ":" + o, "token": tk}, g)
				}
			}
			res, err = d.call("outcome", g, out(cur, action, outcome, tok), oreq(cur, action, outcome, tok))
			ereq["token"] = tok
			if err == nil {
				// side requests, compared with the engine given the same request
				d.call("outcome-repeated", g, out(res.Snapshot, action, outcome, tok), oreq(res.Snapshot, action, outcome, tok))
				if tok > 0 {
					d.call("outcome-stale", g, out(cur, action, outcome, tok-1), oreq(cur, action, outcome, tok-1))
				}
				d.call("outcome-token0", g, out(cur, action, outcome, 0), oreq(cur, action, outcome, 0))
				d.call("outcome-failed", g, out(cur, action, "failed", tok), oreq(cur, action, "failed", tok))
				rec := cur
				rec.At = "__gone__"
				d.call("outcome-recovered", g, out(rec, action, outcome, tok), oreq(rec, action, outcome, tok))
				d.call("outcome-recovered-stale", g, out(rec, action, outcome, tok+1), oreq(rec, action, outcome, tok+1))
			}
		case s.Token != nil:
			kind = "timer"
			name, tok := strings.TrimPrefix(ev, "timer:"), *s.Token
			tm := func(sn figram.Snapshot, tk uint64) func(b *figram.Bot, g map[string]bool) (*figram.Result, error) {
				return func(b *figram.Bot, g map[string]bool) (*figram.Result, error) { return b.Timer(sn, name, tk, g) }
			}
			treq := func(sn figram.Snapshot, tk uint64) func(g map[string]bool) map[string]any {
				return func(g map[string]bool) map[string]any {
					return withGuards(map[string]any{"op": "step", "snapshot": sn, "event": ev, "token": tk}, g)
				}
			}
			res, err = d.call("timer", g, tm(cur, tok), treq(cur, tok))
			ereq["token"] = tok
			d.call("timer-stale", g, tm(cur, tok+1), treq(cur, tok+1))
		default:
			res, err = d.call("step", g,
				func(b *figram.Bot, gg map[string]bool) (*figram.Result, error) { return b.Step(cur, ev, gg) },
				func(gg map[string]bool) map[string]any {
					return withGuards(map[string]any{"op": "step", "snapshot": cur, "event": ev}, gg)
				})
		}
		want := d.eng.do(ereq)
		chainCompare(kind, res, err, want)
		if err != nil {
			st.add("scenario:step-error:"+kind, err.Error())
			return
		}
		// an outcome for an action without outcomes (a plain call of this step), with the step's seq
		for _, a := range res.Actions {
			if a.Kind == figram.Call && !a.Outcomes {
				sn, nm := res.Snapshot, a.Name
				d.call("outcome-no-outcomes", nil,
					func(b *figram.Bot, gg map[string]bool) (*figram.Result, error) { return b.Outcome(sn, nm, "done", sn.Seq, gg) },
					func(gg map[string]bool) map[string]any {
						return withGuards(map[string]any{"op": "step", "snapshot": sn, "event": "call:" + nm + ":done", "token": sn.Seq}, gg)
					})
				break
			}
		}
		newE := rawSnapshot(want)
		// side branches: restart and a recovered step, both as the same request and on the engine chain
		if rng.Intn(4) == 0 {
			sn := res.Snapshot
			r2, e2 := d.call("restart", nil,
				func(b *figram.Bot, _ map[string]bool) (*figram.Result, error) { return b.Restart(sn) },
				func(map[string]bool) map[string]any { return map[string]any{"op": "restart", "snapshot": sn} })
			chainCompare("restart", r2, e2, d.eng.do(map[string]any{"op": "restart", "snapshot": newE}))
		}
		if rng.Intn(6) == 0 && i+1 < len(t.Steps) && t.Steps[i+1].Token == nil {
			nev, ng := t.Steps[i+1].Event, t.Steps[i+1].Guards
			sn := res.Snapshot
			sn.At = "__gone__"
			r2, e2, fg := d.callG("recovered", ng,
				func(b *figram.Bot, gg map[string]bool) (*figram.Result, error) { return b.Step(sn, nev, gg) },
				func(gg map[string]bool) map[string]any {
					return withGuards(map[string]any{"op": "step", "snapshot": sn, "event": nev}, gg)
				})
			chainCompare("recovered", r2, e2, d.eng.do(withGuards(map[string]any{"op": "step", "snapshot": gone(newE), "event": nev}, fg)))
		}
		snap, snapE = res.Snapshot, newE
	}
	st.add("scenario:done", "")
}

func main() {
	workers := flag.Int("workers", 32, "goroutines")
	rounds := flag.Int("rounds", 1, "rounds over all scenarios")
	reload := flag.Bool("reload", false, "reload the documents while requests run")
	procs := flag.Int("procs", 2, "engine processes per document")
	flag.StringVar(&negctl, "negctl", "", "negative control: flip-seq (corrupt ffl-go's result)")
	flag.Parse()
	idx, err := os.ReadFile(flag.Arg(0))
	if err != nil {
		panic(err)
	}
	dir := flag.Arg(0)[:strings.LastIndexByte(flag.Arg(0), '/')]
	var jobs []job
	var docs []*docw
	for _, l := range strings.Split(strings.TrimSpace(string(idx)), "\n") {
		path := strings.Split(l, "\t")[0]
		if !strings.HasPrefix(path, "/") {
			path = dir + "/../" + path
		}
		text, err := os.ReadFile(path)
		if err != nil {
			panic(err)
		}
		d := &docw{path: path, text: string(text), eng: &pool{}}
		for i := 0; i < *procs; i++ {
			d.eng.ps = append(d.eng.ps, startProc(path))
		}
		d.reload()
		docs = append(docs, d)
		name := path[strings.LastIndexByte(path, '/')+1 : len(path)-len(".bot.json")]
		f, err := os.ReadFile(dir + "/" + name + ".jsonl")
		if err != nil {
			panic(err)
		}
		for n, line := range strings.Split(strings.TrimSpace(string(f)), "\n") {
			var t trace
			if err := json.Unmarshal([]byte(line), &t); err != nil {
				panic(err)
			}
			jobs = append(jobs, job{d: d, t: t, n: n})
		}
	}
	fmt.Printf("documents %d, scenarios %d, workers %d, rounds %d, reload %v, GOMAXPROCS %d\n",
		len(docs), len(jobs), *workers, *rounds, *reload, runtime.GOMAXPROCS(0))
	ch := make(chan job)
	var wg sync.WaitGroup
	for w := 0; w < *workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			rng := rand.New(rand.NewSource(int64(w)))
			for j := range ch {
				runScenario(j, rng)
			}
		}(w)
	}
	stop := make(chan struct{})
	var rwg sync.WaitGroup
	if *reload {
		rwg.Add(1)
		go func() {
			defer rwg.Done()
			rng := rand.New(rand.NewSource(99))
			for {
				select {
				case <-stop:
					return
				default:
				}
				docs[rng.Intn(len(docs))].reload()
				time.Sleep(time.Millisecond)
			}
		}()
	}
	// watchdog: no progress for 60 s is a deadlock
	go func() {
		last := int64(-1)
		for {
			time.Sleep(60 * time.Second)
			p := progress.Load()
			if p == last {
				buf := make([]byte, 1<<20)
				n := runtime.Stack(buf, true)
				fmt.Fprintf(os.Stderr, "DEADLOCK? no progress for 60 s\n%s\n", buf[:n])
				os.Exit(3)
			}
			last = p
		}
	}()
	start := time.Now()
	for r := 0; r < *rounds; r++ {
		for _, i := range rand.New(rand.NewSource(int64(r))).Perm(len(jobs)) {
			ch <- jobs[i]
		}
	}
	close(ch)
	wg.Wait()
	close(stop)
	rwg.Wait()
	var loads int64
	for _, d := range docs {
		loads += d.loads.Load()
		d.cur.Load().b.Close()
	}
	keys := make([]string, 0, len(st.counts))
	for k := range st.counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	bad := 0
	for _, k := range keys {
		fmt.Printf("%-48s %d\n", k, st.counts[k])
		if ex := st.examples[k]; ex != "" {
			fmt.Printf("      e.g. %s\n", ex)
		}
		if strings.Contains(k, "DIFF") {
			bad += st.counts[k]
		}
	}
	fmt.Printf("loads %d, ErrClosed retries of a whole step %d, calls %d, %.1fs\n", loads, retries.Load(), progress.Load(), time.Since(start).Seconds())
	if st.counts["same-request:equal"] == 0 || st.counts["scenario:done"] == 0 {
		fmt.Println("FAIL: zero cases")
		os.Exit(1)
	}
	if bad > 0 {
		fmt.Printf("FAIL: %d differences\n", bad)
		os.Exit(1)
	}
}
