package figram

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

const botsDir = "/home/user/figram-audit/hosts/shared/bots/"

func doc(t testing.TB, name string) []byte {
	b, err := os.ReadFile(botsDir + name + ".bot.json")
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func req(t testing.TB, m *Machine, line string) map[string]any {
	r, err := m.Request([]byte(line))
	if err != nil {
		t.Fatalf("request %s: %v", line, err)
	}
	var out map[string]any
	if err := json.Unmarshal(r, &out); err != nil {
		t.Fatalf("reply %q: %v", r, err)
	}
	return out
}

func TestInitConcurrentOnce(t *testing.T) {
	var wg sync.WaitGroup
	errs := make(chan error, 32)
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); errs <- Init() }()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	info := InitReport()
	t.Logf("init: %+v", info)
	if info.Code != 0 {
		t.Fatalf("figram_init = %d", info.Code)
	}
	if info.SigaltstackChange && !info.Restored && os.Getenv("FIGRAM_KEEP_LEAN_SIGALTSTACK") != "1" {
		t.Fatal("Lean changed the M's sigaltstack and the binding did not restore it")
	}
}

func TestBasicProtocol(t *testing.T) {
	m, err := Load("pizza", doc(t, "pizza"))
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	h := req(t, m, `{"id":1,"op":"hello"}`)
	if h["protocol"] != float64(1) {
		t.Fatalf("hello: %v", h)
	}
	s := req(t, m, `{"id":2,"op":"start"}`)
	snap, _ := json.Marshal(s["snapshot"])
	st := req(t, m, fmt.Sprintf(`{"id":3,"op":"step","snapshot":%s,"event":"btn:order"}`, snap))
	if st["snapshot"].(map[string]any)["at"] != "catalog" || st["id"] != float64(3) {
		t.Fatalf("step: %v", st)
	}
}

func TestLoadInvalidDocGivesReason(t *testing.T) {
	_, err := Load("bad", []byte(`{"figram":1,"bot":{"initial":"x","children":{"y":{}}}}`))
	if err == nil || !strings.Contains(err.Error(), "rejected") {
		t.Fatalf("want rejection with reason, got %v", err)
	}
	t.Log(err)
}

// The C ABI has no length parameter: a raw NUL would truncate the request on the
// C side. The binding refuses such input instead of silently sending a prefix.
func TestNULIsRejectedNotTruncated(t *testing.T) {
	m, err := Load("pizza", doc(t, "pizza"))
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	_, err = m.Request([]byte("{\"id\":1,\"op\":\"hello\"}\x00{\"id\":2,\"op\":\"start\"}"))
	if !errors.Is(err, ErrNUL) {
		t.Fatalf("want ErrNUL, got %v", err)
	}
	if _, err := Load("x", []byte("{}\x00")); !errors.Is(err, ErrNUL) {
		t.Fatalf("Load: want ErrNUL, got %v", err)
	}
	// an escaped NUL is fine and is escaped again on the way back (no truncation of the reply)
	r := req(t, m, `{"id":4,"op":"step","snapshot":{"at":"menu","resume":{"a\u0000b":"c"}},"event":"btn:none"}`)
	res := r["snapshot"].(map[string]any)["resume"].(map[string]any)
	if _, ok := res["a\x00b"]; !ok {
		t.Fatalf("escaped NUL lost: %v", r)
	}
}

// Invalid UTF-8 passes the C boundary and is replaced by U+FFFD inside Lean
// (lean_mk_string -> lean_mk_string_lossy_recover): the snapshot the host gets back
// is not the one it sent. Documented behaviour of the native path (cf. FC-03).
func TestInvalidUTF8IsSilentlyReplaced(t *testing.T) {
	m, err := Load("pizza", doc(t, "pizza"))
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	out, err := m.Request([]byte("{\"id\":5,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"resume\":{\"k\xff\":\"v\"}},\"event\":\"btn:none\"}"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(out, []byte("k�")) {
		t.Fatalf("expected U+FFFD replacement, got %q", out)
	}
	out, _ = m.Request([]byte("{\"id\":\"\xff\",\"op\":\"hello\"}"))
	if !bytes.Contains(out, []byte(`"id":"`+"�"+`"`)) {
		t.Fatalf("id echo: %q", out)
	}
	t.Logf("reply id after invalid UTF-8 id: %q", out)
}

type walker struct {
	rng  *rand.Rand
	snap json.RawMessage
}

var pizzaEvents = []string{"cmd:start", "btn:order", "btn:pizza", "btn:size", "btn:add", "btn:cart", "btn:checkout",
	"msg:text", "payment:success", "btn:cancel", "cmd:help", "btn:back", "timer:cart_reminder"}

// line builds a deterministic request sequence from a seed.
func (w *walker) next(id int) []byte {
	ev := pizzaEvents[w.rng.Intn(len(pizzaEvents))]
	g := map[string]bool{"cart_not_empty": w.rng.Intn(2) == 0, "in_delivery_zone": w.rng.Intn(2) == 0}
	r := map[string]any{"id": id, "op": "step", "snapshot": w.snap, "event": ev, "guards": g}
	if strings.HasPrefix(ev, "timer:") {
		r["token"] = w.rng.Intn(8)
	}
	b, _ := json.Marshal(r)
	return b
}

func walk(t testing.TB, m *Machine, seed int64, n int) []string {
	w := &walker{rng: rand.New(rand.NewSource(seed)), snap: json.RawMessage(`{"at":"menu","seq":1}`)}
	var out []string
	for i := 0; i < n; i++ {
		r, err := m.Request(w.next(i))
		if err != nil {
			t.Fatal(err)
		}
		var rep map[string]json.RawMessage
		json.Unmarshal(r, &rep)
		if s, ok := rep["snapshot"]; ok {
			w.snap = s
		}
		out = append(out, string(r))
	}
	return out
}

// Many goroutines on one machine give exactly the sequential results.
func TestConcurrentDeterminism(t *testing.T) {
	m, err := Load("pizza", doc(t, "pizza"))
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	const G, N = 16, 200
	want := make([][]string, G)
	for g := 0; g < G; g++ {
		want[g] = walk(t, m, int64(g), N)
	}
	var wg sync.WaitGroup
	var bad atomic.Int64
	for g := 0; g < G; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			got := walk(t, m, int64(g), N)
			for i := range got {
				if got[i] != want[g][i] {
					bad.Add(1)
				}
			}
		}(g)
	}
	wg.Wait()
	if bad.Load() != 0 {
		t.Fatalf("%d replies differ from the sequential run", bad.Load())
	}
}

// Close while requests are in flight: every request either completes normally or
// fails with ErrClosed; Close returns only after in-flight calls left C.
func TestCloseDuringRequests(t *testing.T) {
	for round := 0; round < 20; round++ {
		m, err := Load("pizza", doc(t, "pizza"))
		if err != nil {
			t.Fatal(err)
		}
		var wg sync.WaitGroup
		var ok, closed atomic.Int64
		for g := 0; g < 8; g++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for i := 0; i < 200; i++ {
					r, err := m.Request([]byte(`{"id":1,"op":"start"}`))
					if errors.Is(err, ErrClosed) {
						closed.Add(1)
						return
					}
					if err != nil || !bytes.Contains(r, []byte(`"at":"menu"`)) {
						t.Errorf("bad reply %q %v", r, err)
						return
					}
					ok.Add(1)
				}
			}()
		}
		for ok.Load() < 50 && closed.Load() == 0 {
			runtime.Gosched()
		}
		m.Close()
		if n := m.Inflight(); n != 0 {
			t.Fatalf("Close returned with %d in flight", n)
		}
		wg.Wait()
		m.Close() // idempotent
	}
}

// Document reload under load: a fresh machine is loaded and swapped in 60 times
// while 8 goroutines keep stepping; the old one is closed after its in-flight calls.
func TestReloadUnderLoad(t *testing.T) {
	d := doc(t, "pizza")
	var mu sync.RWMutex
	cur, err := Load("pizza", d)
	if err != nil {
		t.Fatal(err)
	}
	get := func() *Machine { mu.RLock(); defer mu.RUnlock(); return cur }
	stop := make(chan struct{})
	var wg sync.WaitGroup
	var calls, retried atomic.Int64
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			w := &walker{rng: rand.New(rand.NewSource(int64(g))), snap: json.RawMessage(`{"at":"menu","seq":1}`)}
			for i := 0; ; i++ {
				select {
				case <-stop:
					return
				default:
				}
				line := w.next(i)
				var r []byte
				var err error
				for {
					r, err = get().Request(line)
					if !errors.Is(err, ErrClosed) {
						break
					}
					retried.Add(1)
				}
				if err != nil {
					t.Errorf("request: %v", err)
					return
				}
				var rep map[string]json.RawMessage
				if json.Unmarshal(r, &rep) != nil {
					t.Errorf("bad reply %q", r)
					return
				}
				if s, ok := rep["snapshot"]; ok {
					w.snap = s
				}
				calls.Add(1)
			}
		}(g)
	}
	before := GetStats()
	for i := 0; i < 60; i++ {
		nm, err := Load("pizza", d)
		if err != nil {
			t.Fatal(err)
		}
		mu.Lock()
		old := cur
		cur = nm
		mu.Unlock()
		old.Close()
	}
	close(stop)
	wg.Wait()
	cur.Close()
	after := GetStats()
	t.Logf("calls=%d retried_after_close=%d loads=%d unloads=%d live=%d", calls.Load(), retried.Load(),
		after.Loads-before.Loads, after.Unloads-before.Unloads, after.Live)
	if after.Unloads-before.Unloads != 61 {
		t.Fatalf("unloads %d", after.Unloads-before.Unloads)
	}
}

func BenchmarkHello(b *testing.B) {
	m, err := Load("pizza", doc(b, "pizza"))
	if err != nil {
		b.Fatal(err)
	}
	defer m.Close()
	line := []byte(`{"id":1,"op":"hello"}`)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := m.Request(line); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkStep(b *testing.B) {
	m, err := Load("pizza", doc(b, "pizza"))
	if err != nil {
		b.Fatal(err)
	}
	defer m.Close()
	line := []byte(`{"id":7,"op":"step","snapshot":{"at":"cart","resume":{"order":"cart"},"timers":{"cart_reminder":5},"seq":5},"event":"btn:checkout","guards":{"cart_not_empty":true}}`)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := m.Request(line); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkStepParallel(b *testing.B) {
	m, err := Load("pizza", doc(b, "pizza"))
	if err != nil {
		b.Fatal(err)
	}
	defer m.Close()
	line := []byte(`{"id":7,"op":"step","snapshot":{"at":"cart","resume":{"order":"cart"},"timers":{"cart_reminder":5},"seq":5},"event":"btn:checkout","guards":{"cart_not_empty":true}}`)
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			if _, err := m.Request(line); err != nil {
				b.Fatal(err)
			}
		}
	})
}

func BenchmarkLoadUnload(b *testing.B) {
	d := doc(b, "pizza")
	for i := 0; i < b.N; i++ {
		m, err := Load("pizza", d)
		if err != nil {
			b.Fatal(err)
		}
		m.Close()
	}
}

func BenchmarkCgoNoop(b *testing.B) {
	x := 0
	for i := 0; i < b.N; i++ {
		x = cgoNoop(x)
	}
}

// Negative control for the in-flight guard: unloading without waiting while other
// goroutines keep calling into C on the old automaton (use-after-free). Runs in a
// child process because the expected outcome is a crash or corrupted replies.
func TestUnsafeUnloadIsRed(t *testing.T) {
	if os.Getenv("FIGRAM_UNSAFE_CHILD") == "1" {
		unsafeChild()
		return
	}
	if testing.Short() {
		t.Skip("short")
	}
	cmd := exec.Command(os.Args[0], "-test.run", "^TestUnsafeUnloadIsRed$", "-test.v")
	cmd.Env = append(os.Environ(), "FIGRAM_UNSAFE_CHILD=1")
	out, err := cmd.CombinedOutput()
	tail := string(out)
	if len(tail) > 1500 {
		tail = tail[len(tail)-1500:]
	}
	t.Logf("child: err=%v\n%s", err, tail)
	if err == nil && !strings.Contains(string(out), "BAD_REPLIES=") {
		t.Fatal("child neither crashed nor reported")
	}
	if err == nil && strings.Contains(string(out), "BAD_REPLIES=0 ") {
		t.Fatal("unsafe unload produced no visible fault (control not red)")
	}
}

func unsafeChild() {
	d, _ := os.ReadFile(botsDir + "pizza.bot.json")
	var mu sync.RWMutex
	cur, _ := Load("pizza", d)
	get := func() *Machine { mu.RLock(); defer mu.RUnlock(); return cur }
	var bad, calls atomic.Int64
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			line := []byte(`{"id":7,"op":"step","snapshot":{"at":"cart","seq":5},"event":"btn:checkout","guards":{"cart_not_empty":true}}`)
			for {
				select {
				case <-stop:
					return
				default:
				}
				m := get()
				r, err := m.RequestUnsafe(line)
				calls.Add(1)
				if err == nil && !bytes.Contains(r, []byte(`"at":"address"`)) {
					bad.Add(1)
				}
			}
		}()
	}
	for i := 0; i < 2000; i++ {
		nm, _ := Load("pizza", d)
		mu.Lock()
		old := cur
		cur = nm
		mu.Unlock()
		old.CloseUnsafeNoWait()
	}
	close(stop)
	wg.Wait()
	fmt.Printf("BAD_REPLIES=%d CALLS=%d\n", bad.Load(), calls.Load())
}

// Negative control for `go test -race`: with FIGRAM_RACE_CONTROL=1 two goroutines
// share the binding's Machine without its mutex (what a binding without in-flight
// accounting would do); the race detector must report it.
func TestRaceDetectorControl(t *testing.T) {
	if os.Getenv("FIGRAM_RACE_CONTROL") != "1" {
		t.Skip("set FIGRAM_RACE_CONTROL=1")
	}
	m, err := Load("pizza", doc(t, "pizza"))
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for g := 0; g < 2; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				m.inflight++ // deliberately unsynchronised
				m.RequestUnsafe([]byte(`{"id":1,"op":"hello"}`))
				m.inflight--
			}
		}()
	}
	wg.Wait()
	m.Close()
}
