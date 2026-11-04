package main

// Probes of the C ABI and of the reference wrapper as a Go developer would use them.
//
//	hostgo abi unload-raw       — reload with the raw C ABI: swap the handle, unload the old one
//	                              at once while other goroutines may still use it (what
//	                              embedding.md calls undefined behaviour); counts wrong replies
//	hostgo abi unload-wrapper   — the same with the reference wrapper (Close waits for running
//	                              calls; later calls get "bot is closed" and are retried)
//	hostgo abi unload-raw-wait  — the raw C ABI with a per-handle reader count (control)
//	hostgo abi naive            — what Bot.Request returns for need / engine errors, and a naive
//	                              host that only checks err
//	hostgo abi shim-errors      — the shim's own error replies against the reference validator
//	hostgo abi stale            — a handle unloaded more than 1024 unloads ago
//	hostgo abi closed-window    — a need → guards → retry round trip while the machine is reloaded

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"hostgo/rawfg"
	"hostgo/refwrap"
)

func decodeReply(b []byte) (map[string]any, error) {
	var r map[string]any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	err := d.Decode(&r)
	return r, err
}

func abiMain(args []string) {
	if len(args) < 1 {
		fmt.Println("abi probe name required")
		os.Exit(2)
	}
	switch args[0] {
	case "unload-raw":
		unloadRaw(false, dur(args, 10*time.Second))
	case "unload-raw-wait":
		unloadRaw(true, dur(args, 10*time.Second))
	case "unload-wrapper":
		unloadWrapper(dur(args, 10*time.Second))
	case "naive":
		naive()
	case "shim-errors":
		shimErrors()
	case "stale":
		stale()
	case "closed-window":
		closedWindow(dur(args, 5*time.Second))
	case "race-canary": // negative control of the race detector: an unsynchronized counter
		var x int
		var wg sync.WaitGroup
		for i := 0; i < 4; i++ {
			wg.Add(1)
			go func() { defer wg.Done(); for j := 0; j < 1000; j++ { x++ } }()
		}
		wg.Wait()
		fmt.Println("racy counter:", x)
	default:
		fmt.Println("unknown probe")
		os.Exit(2)
	}
}

func dur(args []string, d time.Duration) time.Duration {
	if len(args) > 1 {
		if x, err := time.ParseDuration(args[1]); err == nil {
			return x
		}
	}
	return d
}

// the request used by the probes: an outcome with 12 000 waits (~420 KB), so that the shim spends
// a while validating UTF-8 between reading the machine pointer and taking its reference
func probeLine() ([]byte, any) {
	seq := int64(5_000_000)
	snap := pointsSnapshot(12000, seq)
	line := lineOf(map[string]any{"id": 7, "op": "step", "snapshot": snap, "event": "call:award:ok", "token": seq})
	// expected reply from a fresh machine
	return line, nil
}

type handleBox struct {
	h       rawfg.Handle
	readers atomic.Int64
	gone    atomic.Bool
}

func unloadRaw(wait bool, d time.Duration) {
	doc := docText("points@1")
	line, _ := probeLine()
	ref, _ := rawfg.Load(doc)
	want := rawfg.Request(ref, line)
	rawfg.Unload(ref)
	var cur atomic.Pointer[handleBox]
	h, _ := rawfg.Load(doc)
	cur.Store(&handleBox{h: h})
	var ok, unloadedErr, wrong, reloads atomic.Int64
	var sample atomic.Value
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				b := cur.Load()
				if wait {
					b.readers.Add(1)
					if b.gone.Load() { // the reloader already retired it
						b.readers.Add(-1)
						continue
					}
				}
				out := rawfg.Request(b.h, line)
				if wait {
					b.readers.Add(-1)
				}
				switch {
				case bytes.Equal(out, want):
					ok.Add(1)
				case bytes.Contains(out, []byte(`"code":"bad_request"`)) && bytes.Contains(out, []byte("already unloaded")):
					unloadedErr.Add(1)
				default:
					wrong.Add(1)
					sample.Store(string(out[:min(len(out), 300)]))
				}
			}
		}()
	}
	t0 := time.Now()
	for time.Since(t0) < d {
		nh, err := rawfg.Load(doc)
		if err != nil {
			panic(err)
		}
		old := cur.Swap(&handleBox{h: nh})
		if wait {
			old.gone.Store(true)
			for old.readers.Load() != 0 {
				time.Sleep(10 * time.Microsecond)
			}
		}
		rawfg.Unload(old.h)
		reloads.Add(1)
		time.Sleep(200 * time.Microsecond)
	}
	close(stop)
	wg.Wait()
	s, _ := sample.Load().(string)
	fmt.Printf("raw C ABI, wait-for-readers=%v: reloads %d, correct replies %d, 'already unloaded' errors %d, WRONG replies %d %s\n",
		wait, reloads.Load(), ok.Load(), unloadedErr.Load(), wrong.Load(), s)
	if wrong.Load() > 0 {
		os.Exit(1)
	}
}

func unloadWrapper(d time.Duration) {
	doc := string(docText("points@1"))
	seq := int64(5_000_000)
	req := map[string]any{"op": "step", "snapshot": json.RawMessage(mustJSON(pointsSnapshot(12000, seq))), "event": "call:award:ok", "token": seq}
	ref, _ := refwrap.Load(doc)
	want, _, _ := ref.Request(req)
	ref.Close()
	var cur atomic.Pointer[refwrap.Bot]
	b, _ := refwrap.Load(doc)
	cur.Store(b)
	var ok, closed, wrong, reloads atomic.Int64
	var maxClose atomic.Int64
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				r, v, err := cur.Load().Request(req)
				switch {
				case err != nil && err.Error() == "bot is closed":
					closed.Add(1)
				case err == nil && v == "result" && fmt.Sprint(r["snapshot"]) == fmt.Sprint(want["snapshot"]):
					ok.Add(1)
				default:
					wrong.Add(1)
				}
			}
		}()
	}
	t0 := time.Now()
	for time.Since(t0) < d {
		nb, err := refwrap.Load(doc)
		if err != nil {
			panic(err)
		}
		old := cur.Swap(nb)
		tc := time.Now()
		old.Close()
		if c := time.Since(tc).Microseconds(); c > maxClose.Load() {
			maxClose.Store(c)
		}
		reloads.Add(1)
		time.Sleep(200 * time.Microsecond)
	}
	close(stop)
	wg.Wait()
	fmt.Printf("reference wrapper: reloads %d, correct replies %d, 'bot is closed' %d (retried by a host), wrong %d, longest Close %d µs\n",
		reloads.Load(), ok.Load(), closed.Load(), wrong.Load(), maxClose.Load())
	if wrong.Load() > 0 {
		os.Exit(1)
	}
}

// naive: Bot.Request reports need and engine errors as a variant with err == nil. A host that
// checks only err (the usual Go idiom) and stores reply["snapshot"] stores null.
func naive() {
	b, _ := refwrap.Load(string(docText("shop@1")))
	defer b.Close()
	cases := []map[string]any{
		{"op": "step", "snapshot": map[string]any{"at": "cart", "seq": 3}, "event": "btn:checkout"},             // need
		{"op": "step", "snapshot": map[string]any{"at": "cart", "seq": -1}, "event": "btn:checkout"},            // bad_snapshot
		{"op": "step", "snapshot": map[string]any{"at": "cart", "seq": 3}, "event": "call:create_order"},        // bad_request
		{"op": "step", "snapshot": map[string]any{"at": "cart", "seq": 9007199254740991}, "event": "btn:more"}, // seq_overflow
	}
	for _, req := range cases {
		r, v, err := b.Request(req)
		// the naive host:
		stored := mustJSON(r["snapshot"])
		fmt.Printf("Request(%s %v) → variant=%q err=%v; reply=%v; a host that checks only err stores snapshot=%s\n",
			req["event"], req["snapshot"], v, err, r, stored)
	}
}

func shimErrors() {
	h, _ := rawfg.Load(docText("pizza@1"))
	rawfg.Unload(h)
	replies := map[string][]byte{
		"unloaded handle": rawfg.Request(h, []byte(`{"id":1,"op":"hello"}`)),
		"empty request":   rawfg.Request(rawfg.Handle{}, nil),
	}
	h2, _ := rawfg.Load(docText("pizza@1"))
	replies["empty request (live)"] = rawfg.Request(h2, []byte{})
	replies["NUL byte"] = rawfg.Request(h2, []byte("{\"id\":1,\x00}"))
	replies["invalid UTF-8"] = rawfg.Request(h2, []byte("{\"id\":1,\"op\":\"he\xffllo\"}"))
	for name, out := range replies {
		r, _ := decodeReply(out)
		v, err := refwrap.CheckReply(r, 1, "hello")
		fmt.Printf("%-22s %s → reference checkReply: variant=%q err=%v\n", name, out, v, err)
	}
}

// stale: a request through a handle unloaded more than 1024 unloads ago is answered by whatever
// machine took its slot (documented as undefined behaviour).
func stale() {
	a, _ := rawfg.Load(docText("pizza@1"))
	rawfg.Unload(a)
	fmt.Printf("right after unload: %s\n", rawfg.Request(a, []byte(`{"id":1,"op":"hello"}`)))
	var live []rawfg.Handle
	for i := 0; i < 1100; i++ { // 1100 more unloads: the quarantine (1024) releases the slot of a
		x, _ := rawfg.Load(docText("points@1"))
		rawfg.Unload(x)
	}
	for i := 0; i < 200; i++ { // new machines take the released slots
		x, _ := rawfg.Load(docText("points@1"))
		live = append(live, x)
	}
	fmt.Printf("after 1100 more unloads and 200 loads: the stale pizza handle answers %s (points has 6 messages, pizza 13)\n",
		rawfg.Request(a, []byte(`{"id":1,"op":"hello"}`)))
	for _, x := range live {
		rawfg.Unload(x)
	}
}

// closedWindow: a step that needs conditions makes two requests on the same machine; a reload
// between them closes it. The host retries the whole step on the new machine.
func closedWindow(d time.Duration) {
	doc := string(docText("shop@1"))
	reg := &Registry{}
	dv, _ := loadDoc("shop@1")
	reg.ptr("shop").Store(dv)
	var steps, wrong atomic.Int64
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; ; i++ {
				select {
				case <-stop:
					return
				default:
				}
				want := i%2 == 0
				rep, err := reg.call("shop", map[string]any{"op": "step", "snapshot": map[string]any{"at": "cart", "seq": 3}, "event": "btn:checkout"},
					func(n []string) map[string]bool {
						time.Sleep(300 * time.Microsecond) // conditions are read from the database
						return map[string]bool{"cart_not_empty": want}
					})
				steps.Add(1)
				at := ""
				if err == nil && rep.Snapshot != nil {
					at = rep.Snapshot["at"].(string)
				}
				if err != nil || (want && at != "address") || (!want && at != "catalog") {
					wrong.Add(1)
				}
			}
		}(g)
	}
	t0 := time.Now()
	n := 0
	for time.Since(t0) < d {
		reg.Install(nil, "shop@1")
		n++
		time.Sleep(time.Millisecond)
	}
	close(stop)
	wg.Wait()
	reg.CloseAll()
	_ = doc
	fmt.Printf("need/guards round trips under reload: reloads %d, steps %d, 'bot is closed' retries %d, wrong %d\n", n, steps.Load(), reg.closed.Load(), wrong.Load())
	if wrong.Load() > 0 || reg.closed.Load() == 0 {
		os.Exit(1)
	}
	_ = strings.Contains
}
