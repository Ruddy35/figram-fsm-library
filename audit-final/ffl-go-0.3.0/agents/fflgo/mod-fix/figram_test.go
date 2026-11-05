package figram

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func readDoc(t testing.TB, name string) string {
	t.Helper()
	b, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func load(t testing.TB, name string) *Bot {
	t.Helper()
	b, err := Load(readDoc(t, name))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(b.Close)
	return b
}

// A scenario of testdata/golden.jsonl (written by `figram golden` of the same release).
type scenario struct {
	Doc   string `json:"doc"`
	Name  string `json:"name"`
	Start *struct {
		Event    *string         `json:"event"` // the request is start with this first event
		Guards   map[string]bool `json:"guards"`
		Snapshot Snapshot        `json:"snapshot"`
		Actions  []Action        `json:"actions"`
	} `json:"start"`
	From  *Snapshot `json:"from"`
	Steps []struct {
		Event     string          `json:"event"`
		Token     *uint64         `json:"token"`
		Guards    map[string]bool `json:"guards"`
		Snapshot  Snapshot        `json:"snapshot"`
		Actions   []Action        `json:"actions"`
		Recovered bool            `json:"recovered"`
	} `json:"steps"`
}

func scenarios(t testing.TB) []scenario {
	f, err := os.Open("testdata/golden.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []scenario
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	for sc.Scan() {
		var s scenario
		dec := json.NewDecoder(strings.NewReader(sc.Text()))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&s); err != nil {
			t.Fatalf("golden.jsonl: %v", err)
		}
		out = append(out, s)
	}
	if err := sc.Err(); err != nil || len(out) == 0 {
		t.Fatalf("golden.jsonl: %d scenarios, %v", len(out), err)
	}
	return out
}

// sameActions treats nil and empty as equal.
func sameActions(a, b []Action) bool { return len(a) == 0 && len(b) == 0 || reflect.DeepEqual(a, b) }

// run replays a scenario and reports the first difference ("" when it matches).
func run(b *Bot, s scenario) string {
	var snap Snapshot
	if s.Start != nil {
		r, err := b.Start(0)
		if s.Start.Event != nil {
			r, err = b.StartWith(0, *s.Start.Event, s.Start.Guards)
		}
		if err != nil {
			return "start: " + err.Error()
		}
		if !reflect.DeepEqual(r.Snapshot, s.Start.Snapshot) || !sameActions(r.Actions, s.Start.Actions) {
			return "start: got " + dump(r)
		}
		snap = r.Snapshot
	} else {
		snap = *s.From
	}
	for i, st := range s.Steps {
		var r *Result
		var err error
		if st.Token != nil && strings.HasPrefix(st.Event, "call:") {
			// call:<action>:<outcome>: the action may contain ':', the outcome may not
			i := strings.LastIndexByte(st.Event, ':')
			r, err = b.Outcome(snap, st.Event[len("call:"):i], st.Event[i+1:], *st.Token, st.Guards)
		} else if st.Token != nil {
			r, err = b.Timer(snap, strings.TrimPrefix(st.Event, "timer:"), *st.Token, st.Guards)
		} else {
			r, err = b.Step(snap, st.Event, st.Guards)
		}
		if err != nil {
			return st.Event + ": " + err.Error()
		}
		if len(r.Need) != 0 || r.Recovered != st.Recovered || !reflect.DeepEqual(r.Snapshot, st.Snapshot) ||
			!sameActions(r.Actions, st.Actions) {
			return "step " + string(rune('0'+i)) + " " + st.Event + ": got " + dump(r)
		}
		snap = r.Snapshot
	}
	return ""
}

func dump(v any) string { b, _ := json.Marshal(v); return string(b) }

func TestGolden(t *testing.T) {
	bots := map[string]*Bot{}
	for _, s := range scenarios(t) {
		if bots[s.Doc] == nil {
			bots[s.Doc] = load(t, s.Doc)
		}
		if d := run(bots[s.Doc], s); d != "" {
			t.Errorf("%s: %s", s.Name, d)
		}
	}
}

// All scenarios from many goroutines on one Bot per document (run with -race too).
func TestGoldenConcurrent(t *testing.T) {
	all := scenarios(t)
	bots := map[string]*Bot{}
	for _, s := range all {
		if bots[s.Doc] == nil {
			bots[s.Doc] = load(t, s.Doc)
		}
	}
	var wg sync.WaitGroup
	var bad atomic.Int64
	for g := 0; g < 32; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 20; i++ {
				s := all[(g+i)%len(all)]
				if d := run(bots[s.Doc], s); d != "" {
					if bad.Add(1) == 1 {
						t.Errorf("%s: %s", s.Name, d)
					}
				}
			}
		}(g)
	}
	wg.Wait()
	if bad.Load() != 0 {
		t.Fatalf("%d scenario runs differed", bad.Load())
	}
}

func TestHello(t *testing.T) {
	h, err := load(t, "pizza.bot.json").Hello()
	if err != nil || h != (Hello{Protocol: ProtocolVersion, States: 13}) || ProtocolVersion != 3 {
		t.Fatalf("Hello = %+v, %v", h, err)
	}
}

// The session of docs/format.md, section 8, "Protocol".
func TestNeedAndDecide(t *testing.T) {
	b := load(t, "pizza.bot.json")
	cart := Snapshot{At: "cart", Seq: 3}
	r, err := b.Step(cart, "btn:checkout", nil)
	if err != nil || !reflect.DeepEqual(r.Need, []string{"cart_not_empty"}) || r.Snapshot.At != "" || r.Actions != nil {
		t.Fatalf("step without guards = %s, %v", dump(r), err)
	}
	var asked []string
	r, err = Decide(nil, func(c string) (bool, error) { asked = append(asked, c); return true, nil },
		func(g map[string]bool) (*Result, error) { return b.Step(cart, "btn:checkout", g) })
	want := &Result{
		Snapshot: Snapshot{At: "address", Resume: map[string]string{"order": "address"}, Seq: 4},
		Actions:  []Action{{Kind: CancelTimer, Name: "cart_reminder"}, {Kind: Send, Name: "address"}},
	}
	if err != nil || !reflect.DeepEqual(r, want) || !reflect.DeepEqual(asked, []string{"cart_not_empty"}) {
		t.Fatalf("Decide = %s, %v (asked %v)", dump(r), err, asked)
	}
	// An error of eval stops Decide; a given guard is not asked.
	boom := errors.New("boom")
	if _, err := Decide(nil, func(string) (bool, error) { return false, boom },
		func(g map[string]bool) (*Result, error) { return b.Step(cart, "btn:checkout", g) }); err != boom {
		t.Fatalf("Decide with a failing eval: %v", err)
	}
	given := map[string]bool{"cart_not_empty": false}
	r, err = Decide(given, func(c string) (bool, error) { t.Errorf("asked %q", c); return false, nil },
		func(g map[string]bool) (*Result, error) { return b.Step(cart, "btn:checkout", g) })
	if err != nil || r.Snapshot.At == "address" || len(given) != 1 {
		t.Fatalf("Decide with a given guard = %s, %v", dump(r), err)
	}
}

func TestStartSeqAndRestart(t *testing.T) {
	b := load(t, "pizza.bot.json")
	r, err := b.Start(41)
	if err != nil || r.Snapshot.Seq != 42 {
		t.Fatalf("Start(41) = %s, %v", dump(r), err)
	}
	r, err = b.Step(r.Snapshot, "btn:order", nil)
	if err != nil || r.Snapshot.At != "catalog" {
		t.Fatalf("step = %s, %v", dump(r), err)
	}
	r, err = b.Restart(r.Snapshot)
	if err != nil || r.Snapshot.At != "menu" || r.Snapshot.Seq != 44 || r.Snapshot.Resume != nil {
		t.Fatalf("Restart = %s, %v", dump(r), err)
	}
	if _, err := b.Start(MaxInt); !isCode(err, CodeSeqOverflow) {
		t.Fatalf("Start(MaxInt): %v", err)
	}
}

// The first event of a new user (docs/format.md, section 7, decision 9).
func TestStartWith(t *testing.T) {
	b := load(t, "features.bot.json")
	// The variant of the initial message depends on a condition: need, then Decide.
	r, err := b.StartWith(0, "btn:go", nil)
	if err != nil || !reflect.DeepEqual(r.Need, []string{"is_vip"}) || r.Snapshot.At != "" {
		t.Fatalf("StartWith without guards = %s, %v", dump(r), err)
	}
	r, err = Decide(nil, func(string) (bool, error) { return true, nil },
		func(g map[string]bool) (*Result, error) { return b.StartWith(0, "btn:go", g) })
	want := &Result{Snapshot: Snapshot{At: "vip", Timers: map[string]uint64{"nudge": 1}, Seq: 1},
		Actions: []Action{{Kind: StartTimer, Name: "nudge", Ms: 30000, Token: 1}, {Kind: Send, Name: "vip"}}}
	if err != nil || !reflect.DeepEqual(r, want) {
		t.Fatalf("Decide(StartWith) = %s, %v", dump(r), err)
	}
	// Without a variant: the plain start, with the high-water mark.
	r, err = b.StartWith(41, "btn:nothing", nil)
	plain, err2 := b.Start(41)
	if err != nil || err2 != nil || !reflect.DeepEqual(r, plain) || r.Snapshot.Seq != 42 {
		t.Fatalf("StartWith(41, unmatched) = %s, %v; Start(41) = %s, %v", dump(r), err, dump(plain), err2)
	}
	// A timer event and an empty event are rejected.
	if _, err := b.StartWith(0, "timer:idle", nil); !isCode(err, CodeBadRequest) {
		t.Fatalf("StartWith(timer event): %v", err)
	}
	if _, err := b.StartWith(0, "", nil); !isCode(err, CodeBadRequest) {
		t.Fatalf("StartWith(empty event): %v", err)
	}
}

// A step from a removed message processes its event (decision 8): the old timers are cancelled,
// the places kept, and the step may ask for conditions.
func TestRecoveredStep(t *testing.T) {
	b := load(t, "features.bot.json")
	gone := Snapshot{At: "gone", Back: "home", Timers: map[string]uint64{"old": 3}, Seq: 7}
	r, err := b.Step(gone, "btn:go", nil)
	if err != nil || !reflect.DeepEqual(r.Need, []string{"is_vip"}) {
		t.Fatalf("recovered step without guards = %s, %v", dump(r), err)
	}
	r, err = b.Step(gone, "btn:go", map[string]bool{"is_vip": false})
	want := &Result{
		Snapshot:  Snapshot{At: "catalog", Back: "home", Resume: map[string]string{"shop": "catalog"}, Seq: 8},
		Actions:   []Action{{Kind: CancelTimer, Name: "old"}, {Kind: Send, Name: "catalog"}},
		Recovered: true,
	}
	if err != nil || !reflect.DeepEqual(r, want) {
		t.Fatalf("recovered step = %s, %v", dump(r), err)
	}
}

// Load rejects a library of another protocol version.
func TestProtocolMismatch(t *testing.T) {
	if err := protocolMismatch(ProtocolVersion); err != nil {
		t.Fatalf("protocolMismatch(%d) = %v", ProtocolVersion, err)
	}
	if err := protocolMismatch(1); err == nil || !strings.Contains(err.Error(), "protocol 1") {
		t.Fatalf("protocolMismatch(1) = %v", err)
	}
}

func isCode(err error, code string) bool {
	var pe *ProtocolError
	return errors.As(err, &pe) && pe.Code == code
}

func TestProtocolErrors(t *testing.T) {
	b := load(t, "pizza.bot.json")
	menu := Snapshot{At: "menu", Seq: 1}
	cases := []struct {
		name string
		err  error
		code string
	}{
		{"empty at", second(b.Step(Snapshot{}, "btn:order", nil)), CodeBadSnapshot},
		{"version 2", second(b.Step(Snapshot{V: 2, At: "menu"}, "btn:order", nil)), CodeBadSnapshot},
		{"empty resume value", second(b.Step(Snapshot{At: "menu", Resume: map[string]string{"order": ""}}, "btn:order", nil)), CodeBadSnapshot},
		{"empty event", second(b.Step(menu, "", nil)), CodeBadRequest},
		{"timer without a name", second(b.Timer(menu, "", 1, nil)), CodeBadRequest},
		{"seq overflow", second(b.Step(Snapshot{At: "menu", Seq: MaxInt}, "btn:order", nil)), CodeSeqOverflow},
		{"restart seq overflow", second(b.Restart(Snapshot{At: "menu", Seq: MaxInt})), CodeSeqOverflow},
	}
	for _, c := range cases {
		if !isCode(c.err, c.code) {
			t.Errorf("%s: %v, want code %s", c.name, c.err, c.code)
		}
	}
	// An unknown event and a stale timer token change nothing.
	unknown, err1 := b.Step(menu, "btn:nothing", nil)
	stale, err2 := b.Timer(menu, "cart_reminder", 99, nil)
	if err1 != nil || err2 != nil {
		t.Fatal(err1, err2)
	}
	for _, r := range []*Result{unknown, stale} {
		if !reflect.DeepEqual(r.Snapshot, menu) || len(r.Actions) != 0 {
			t.Errorf("ignored event changed the snapshot: %s", dump(r))
		}
	}
}

func second(_ *Result, err error) error { return err }

func TestRawRequest(t *testing.T) {
	b := load(t, "pizza.bot.json")
	cases := map[string]string{
		`{"id":1,"op":"hello"}`: `{"id":1,"protocol":3,"states":13}`,
		`{"id":8,"op":"step","snapshot":{"at":"cart"},"event":"btn:checkout","gaurds":{}}`: `{"id":8,"error":"unknown request field ` +
			"`gaurds`" + `","code":"bad_request"}`,
		``:    `"code":"parse_error"`,
		`[1]`: `{"id":null,"error":"request must be an object","code":"bad_request"}`,
	}
	for req, want := range cases {
		out, err := b.Request([]byte(req))
		if err != nil || !strings.Contains(string(out), want) {
			t.Errorf("Request(%q) = %s, %v; want %s", req, out, err, want)
		}
	}
	if out, err := b.Request([]byte("{\"id\":1,\"op\":\"hello\"}\x00")); err != nil || !strings.Contains(string(out), "parse_error") {
		t.Errorf("NUL in a request: %s, %v", out, err)
	}
}

func TestTooLarge(t *testing.T) {
	b := load(t, "pizza.bot.json")
	big := "btn:" + strings.Repeat("x", 2<<20)
	if _, err := b.Step(Snapshot{At: "menu"}, big, nil); !isCode(err, CodeTooLarge) {
		t.Fatalf("2 MiB event: %v", err)
	}
	if prev := SetMaxRequest(0); prev != 1<<20 {
		t.Errorf("default limit %d, want %d", prev, 1<<20)
	}
	defer SetMaxRequest(1 << 20)
	if _, err := b.Step(Snapshot{At: "menu"}, big, nil); err != nil {
		t.Fatalf("no limit: %v", err)
	}
}

func TestCheckAndFmt(t *testing.T) {
	for _, name := range []string{"pizza.bot.json", "features.bot.json", "annotations.bot.json"} {
		doc := readDoc(t, name)
		if err := Check(doc); err != nil {
			t.Errorf("%s: %v", name, err)
		}
		f, err := Fmt(doc)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if f2, err := Fmt(f); err != nil || f2 != f {
			t.Errorf("%s: fmt is not idempotent: %v", name, err)
		}
	}
	// A misspelt key is an error; fmt keeps it.
	typo := strings.Replace(readDoc(t, "pizza.bot.json"), `"if"`, `"iff"`, 1)
	var de *DocumentError
	if err := Check(typo); !errors.As(err, &de) || !strings.Contains(strings.Join(de.Problems, "\n"), "unknown key `iff`") {
		t.Fatalf("Check(typo) = %v", err)
	}
	if f, err := Fmt(typo); err != nil || !strings.Contains(f, `"iff"`) {
		t.Fatalf("Fmt(typo) = %v", err)
	}
	if _, err := Load(typo); !errors.As(err, &de) || !strings.Contains(err.Error(), "unknown key `iff`") {
		t.Fatalf("Load(typo) = %v", err)
	}
	for _, bad := range []string{`{"figram":1}`, `not json`, "", "{\"figram\":1}\x00x", "\xff"} {
		if err := Check(bad); !errors.As(err, &de) || len(de.Problems) == 0 {
			t.Errorf("Check(%q) = %v", bad, err)
		}
		if _, err := Fmt(bad); err == nil && bad != `{"figram":1}` {
			t.Errorf("Fmt(%q) accepted", bad)
		}
		if b, err := Load(bad); !errors.As(err, &de) {
			t.Errorf("Load(%q) = %v", bad, err)
			if b != nil {
				b.Close()
			}
		}
	}
}

func TestActionJSON(t *testing.T) {
	acts := []Action{
		{Kind: Send, Name: "menu"}, {Kind: Call, Name: "pay"}, {Kind: CancelTimer, Name: "t"},
		{Kind: StartTimer, Name: "t", Ms: 0, Token: 7},
		{Kind: Call, Name: "create_order", Token: 6, Outcomes: true}, {Kind: Call, Name: "log", Detached: true},
		{Kind: Call, Name: "ping", Token: 8, Outcomes: true, Detached: true},
	}
	b, err := json.Marshal(acts)
	if err != nil || string(b) != `[{"send":"menu"},{"call":"pay"},{"cancel_timer":"t"},{"start_timer":"t","ms":0,"token":7},`+
		`{"call":"create_order","token":6},{"call":"log","detached":true},{"call":"ping","token":8,"detached":true}]` {
		t.Fatalf("Marshal = %s, %v", b, err)
	}
	var back []Action
	if err := json.Unmarshal(b, &back); err != nil || !reflect.DeepEqual(back, acts) {
		t.Fatalf("Unmarshal = %+v, %v", back, err)
	}
	for _, bad := range []string{`{"send":""}`, `{"send":"a","call":"b"}`, `{"start_timer":"t","ms":-1,"token":1}`,
		`{"call":"a","detached":false}`, `{"call":"a","token":-1}`, `{"call":"a","outcome":"x"}`,
		`{"start_timer":"t","ms":1.5,"token":1}`, `{"start_timer":"t","ms":1}`, `{"jump":"x"}`, `"send"`} {
		var a Action
		if err := json.Unmarshal([]byte(bad), &a); err == nil {
			t.Errorf("Unmarshal(%s) accepted: %+v", bad, a)
		}
	}
	if _, err := json.Marshal(Action{Kind: "jump"}); err == nil {
		t.Error("Marshal of an unknown kind accepted")
	}
}

func TestSnapshotJSON(t *testing.T) {
	s := Snapshot{At: "a", Back: "b", Resume: map[string]string{"s": "a"}, Timers: map[string]uint64{"t": 0},
		Pending: []Wait{{Call: "ping", Token: 5}, {Call: "ping", Token: 4}}, Seq: 5}
	b, _ := json.Marshal(s)
	// The field order of the engine.
	if string(b) != `{"at":"a","back":"b","resume":{"s":"a"},"timers":{"t":0},"pending":[{"call":"ping","token":5},{"call":"ping","token":4}],"seq":5}` {
		t.Fatalf("Marshal = %s", b)
	}
	var back Snapshot
	if err := json.Unmarshal(b, &back); err != nil || !reflect.DeepEqual(back, s) {
		t.Fatalf("Unmarshal = %+v, %v", back, err)
	}
	b, _ = json.Marshal(Snapshot{At: "a"})
	if string(b) != `{"at":"a"}` {
		t.Fatalf("Marshal = %s", b)
	}
}

func TestResultJSON(t *testing.T) {
	cases := map[string]Result{
		`{"need":["c"]}`:                       {Need: []string{"c"}},
		`{"actions":[],"snapshot":{"at":"a"}}`: {Snapshot: Snapshot{At: "a"}},
		`{"actions":[{"send":"a"}],"recovered":true,"snapshot":{"at":"a","seq":2}}`: {
			Snapshot: Snapshot{At: "a", Seq: 2}, Actions: []Action{{Kind: Send, Name: "a"}}, Recovered: true},
	}
	for want, r := range cases {
		if b, err := json.Marshal(r); err != nil || string(b) != want {
			t.Errorf("Marshal(%+v) = %s, %v; want %s", r, b, err, want)
		}
	}
}

// An action with outcomes (docs/format.md, section 7, decision 12): the call carries a token, the
// snapshot waits for it, the outcome with that token is accepted once and handled by the arrows of
// the message the user is on; a stale token changes nothing.
func TestOutcome(t *testing.T) {
	b := load(t, "pizza.bot.json")
	pay := Snapshot{At: "pay", Resume: map[string]string{"order": "pay"}, Seq: 5}
	r, err := b.Step(pay, "payment:success", nil)
	want := &Result{
		Snapshot: Snapshot{At: "placing", Resume: map[string]string{"order": "placing"},
			Pending: []Wait{{Call: "create_order", Token: 6}}, Seq: 6},
		Actions: []Action{{Kind: Call, Name: "create_order", Token: 6, Outcomes: true}, {Kind: Send, Name: "placing"}},
	}
	if err != nil || !reflect.DeepEqual(r, want) {
		t.Fatalf("payment:success = %s, %v", dump(r), err)
	}
	placing := r.Snapshot
	// A stale token: nothing changes.
	if r, err := b.Outcome(placing, "create_order", "created", 5, nil); err != nil ||
		!reflect.DeepEqual(r.Snapshot, placing) || len(r.Actions) != 0 {
		t.Fatalf("stale outcome = %s, %v", dump(r), err)
	}
	// The token of the call: handled by the root's arrows, the wait is gone.
	r, err = b.Outcome(placing, "create_order", "created", 6, nil)
	if err != nil || r.Snapshot.At != "done" || r.Snapshot.Pending != nil ||
		!reflect.DeepEqual(r.Actions, []Action{{Kind: Call, Name: "notify_kitchen", Detached: true}, {Kind: Send, Name: "done"}}) {
		t.Fatalf("outcome = %s, %v", dump(r), err)
	}
	// Delivered again: the wait is gone, nothing changes.
	if again, err := b.Outcome(r.Snapshot, "create_order", "created", 6, nil); err != nil ||
		!reflect.DeepEqual(again.Snapshot, r.Snapshot) || len(again.Actions) != 0 {
		t.Fatalf("repeated outcome = %s, %v", dump(again), err)
	}
	// rejected depends on a condition: Need, then Decide.
	r, err = Decide(nil, func(c string) (bool, error) { return c == "address_rejected", nil },
		func(g map[string]bool) (*Result, error) { return b.Outcome(placing, "create_order", "rejected", 6, g) })
	if err != nil || r.Snapshot.At != "address" {
		t.Fatalf("Decide(rejected) = %s, %v", dump(r), err)
	}
	// A malformed outcome is refused before it is sent; an outcome without a token of the call is
	// a bad_request of the engine.
	for _, c := range [][2]string{{"", "created"}, {"create_order", ""}, {"create_order", "a:b"}} {
		if _, err := b.Outcome(placing, c[0], c[1], 6, nil); err == nil {
			t.Errorf("Outcome(%q, %q) accepted", c[0], c[1])
		}
	}
	if _, err := b.Step(placing, "call:create_order:created", nil); !isCode(err, CodeBadRequest) {
		t.Fatalf("outcome event without a token: %v", err)
	}
	// restart clears the waits.
	if r, err := b.Restart(placing); err != nil || r.Snapshot.Pending != nil {
		t.Fatalf("Restart = %s, %v", dump(r), err)
	}
}

// Under a request size limit the engine refuses a step whose new snapshot would not fit into the
// next request, and the stored snapshot still works for its outcomes.
func TestSnapshotTooLarge(t *testing.T) {
	b := load(t, "features.bot.json")
	s := Snapshot{At: "home", Seq: 10000}
	for i := 0; i < 2000; i++ {
		s.Pending = append(s.Pending, Wait{Call: "ping", Token: uint64(9999 - i)})
	}
	prev := SetMaxRequest(65536)
	defer SetMaxRequest(prev)
	// The user keeps pressing a button whose action waits for every outcome, and none comes.
	var err error
	for i := 0; i < 1000; i++ {
		var r *Result
		if r, err = b.Step(s, "btn:ping", nil); err != nil {
			break
		}
		s = r.Snapshot
	}
	if !isCode(err, CodeSnapshotTooLarge) {
		t.Fatalf("growing steps: %v", err)
	}
	stored, _ := json.Marshal(s)
	if len(stored) > 65536*15/16 {
		t.Fatalf("the engine wrote a snapshot of %d bytes", len(stored))
	}
	// The last snapshot the engine wrote still carries its outcomes and restart.
	n := len(s.Pending)
	if r, err := b.Outcome(s, "ping", "pong", s.Pending[n-1].Token, nil); err != nil || len(r.Snapshot.Pending) != n-1 {
		t.Fatalf("outcome with the stored snapshot: %v", err)
	}
	if r, err := b.Restart(s); err != nil || r.Snapshot.Pending != nil {
		t.Fatalf("restart with the stored snapshot: %v", err)
	}
}

func TestBundledVersion(t *testing.T) {
	b, err := os.ReadFile("lib/linux_amd64/VERSION")
	if err != nil || strings.TrimSpace(string(b)) != BundledVersion {
		t.Fatalf("lib/linux_amd64/VERSION = %q, BundledVersion = %q (%v)", b, BundledVersion, err)
	}
}

// checkReply accepts exactly the protocol's replies.
func TestCheckReply(t *testing.T) {
	cases := []struct{ op, reply, want string }{
		{"step", `{"id":5,"error":"x","code":"bad_request"}`, "error"},
		{"step", `{"id":5,"error":"x","code":"bad_snapshot"}`, "error"},
		{"restart", `{"id":5,"error":"x","code":"seq_overflow"}`, "error"},
		{"step", `{"id":null,"error":"x","code":"too_large"}`, "error"},
		{"step", `{"id":null,"error":"x","code":"parse_error"}`, "error"},
		{"step", `{"id":5,"error":"x","code":"nonsense"}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":7}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":"bad_request","extra":1}`, "ERR"},
		{"step", `{"id":5,"error":1,"code":"bad_request"}`, "ERR"},
		{"step", `{"id":null,"error":"x","code":"bad_request"}`, "ERR"},
		{"step", `{"id":6,"error":"x","code":"bad_request"}`, "ERR"},
		{"step", `{"id":5,"error":"x"}`, "ERR"},
		{"hello", `{"id":5,"protocol":3,"states":11}`, "hello"},
		{"hello", `{"id":5,"protocol":3,"states":11,"mutant":"x"}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":"snapshot_too_large"}`, "error"},
		{"hello", `{"id":5,"protocol":1.5,"states":11}`, "ERR"},
		{"start", `{"id":5,"snapshot":{"at":"menu","seq":8},"actions":[{"send":"menu"}]}`, "result"},
		{"start", `{"id":"5","snapshot":{"at":"menu","seq":8},"actions":[]}`, "ERR"},
		{"start", `{"id":5,"snapshot":{"at":"menu"},"actions":[],"recovered":true}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"menu"},"actions":[],"recovered":true}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"menu"},"actions":[],"recovered":false}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"menu","x":1},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"menu","seq":9007199254740992},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"menu"},"actions":[{"send":""}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"menu"}}`, "ERR"},
		{"step", `{"id":5,"need":["c"]}`, "need"},
		{"step", `{"id":5,"need":[]}`, "ERR"},
		// Protocol 2: start with an event may answer need; restart and hello never do.
		{"start", `{"id":5,"need":["c"]}`, "need"},
		{"start", `{"id":5,"need":[]}`, "ERR"},
		{"restart", `{"id":5,"need":["c"]}`, "ERR"},
		{"hello", `{"id":5,"need":["c"]}`, "ERR"},
		{"restart", `{"id":5,"snapshot":{"at":"menu"},"actions":[],"recovered":true}`, "ERR"},
		// The snapshot field back: a message name.
		{"step", `{"id":5,"snapshot":{"at":"vip","back":"home","seq":1},"actions":[]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"vip","back":"","seq":1},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"vip","back":1,"seq":1},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"vip","back":null,"seq":1},"actions":[]}`, "ERR"},
		// Protocol 3: waits for outcomes in the snapshot, a list of exactly {call, token}.
		{"step", `{"id":5,"snapshot":{"at":"item","pending":[{"call":"check_stock","token":6}],"seq":6},"actions":[]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"ping","token":7},{"call":"ping","token":6}],"seq":7},"actions":[]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[]},"actions":[]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"ping"}]},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"","token":1}]},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"ping","token":-1}]},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"ping","token":"1"}]},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":[{"call":"ping","token":1,"x":1}]},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":{"ping":1}},"actions":[]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","pending":null},"actions":[]}`, "ERR"},
		// Protocol 3: call carries token (an action with outcomes) and detached (a background action).
		{"step", `{"id":5,"snapshot":{"at":"item","seq":6},"actions":[{"call":"check_stock","token":6}]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"log","detached":true}]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"ping","token":1,"detached":true}]}`, "result"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"log","detached":false}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"log","detached":1}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"ping","token":1.5}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"","token":1}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"call":"ping","token":1,"outcome":"pong"}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"send":"home","detached":true}]}`, "ERR"},
		{"step", `{"id":5,"snapshot":{"at":"home","seq":1},"actions":[{"send":"home","token":1}]}`, "ERR"},
	}
	for _, c := range cases {
		v, err := decodeJSON([]byte(c.reply))
		if err != nil {
			t.Fatal(err)
		}
		got, err := checkReply(v.(map[string]any), 5, c.op)
		if err != nil {
			got = "ERR"
		}
		if got != c.want {
			t.Errorf("checkReply(%s, %s) = %q (%v), want %q", c.reply, c.op, got, err, c.want)
		}
	}
}

// Close in the middle of a stream of requests: correct replies before it, ErrClosed after it,
// no panic and no race.
func TestCloseDuringRequests(t *testing.T) {
	doc := readDoc(t, "pizza.bot.json")
	var ok, closed atomic.Int64
	for round := 0; round < 50; round++ {
		b, err := Load(doc)
		if err != nil {
			t.Fatal(err)
		}
		var wg sync.WaitGroup
		stop := make(chan struct{})
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
					_, err := b.Step(Snapshot{At: "menu"}, "btn:order", nil)
					switch {
					case err == nil:
						ok.Add(1)
					case errors.Is(err, ErrClosed):
						closed.Add(1)
					default:
						t.Errorf("unexpected: %v", err)
						return
					}
				}
			}()
		}
		time.Sleep(time.Millisecond)
		var cw sync.WaitGroup
		for c := 0; c < 3; c++ {
			cw.Add(1)
			go func() { defer cw.Done(); b.Close() }()
		}
		cw.Wait()
		time.Sleep(time.Millisecond)
		close(stop)
		wg.Wait()
		if _, err := b.Hello(); !errors.Is(err, ErrClosed) {
			t.Fatalf("Hello after Close: %v", err)
		}
	}
	if ok.Load() == 0 || closed.Load() == 0 {
		t.Fatalf("both sides not exercised: ok=%d closed=%d", ok.Load(), closed.Load())
	}
}
