package host

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"figramhost/figram"
)

// DocInfo is what the host knows about a document, used to validate engine replies.
type DocInfo struct {
	Name     string
	Messages map[string]bool
	Sections map[string]bool
	Timers   map[string]map[int64]bool // timer name -> allowed delays
	Calls    map[string]bool
}

func parseDocInfo(name string, doc []byte) (*DocInfo, error) {
	var d struct {
		Calls map[string]any `json:"calls"`
		Bot   map[string]any `json:"bot"`
	}
	if err := json.Unmarshal(doc, &d); err != nil {
		return nil, err
	}
	di := &DocInfo{Name: name, Messages: map[string]bool{}, Sections: map[string]bool{},
		Timers: map[string]map[int64]bool{}, Calls: map[string]bool{}}
	for c := range d.Calls {
		di.Calls[c] = true
	}
	var walk func(n string, node map[string]any)
	walk = func(n string, node map[string]any) {
		if ch, ok := node["children"].(map[string]any); ok {
			di.Sections[n] = true
			for k, v := range ch {
				if m, ok := v.(map[string]any); ok {
					walk(k, m)
				}
			}
			return
		}
		di.Messages[n] = true
		if ts, ok := node["timers"].(map[string]any); ok {
			for t, ms := range ts {
				if di.Timers[t] == nil {
					di.Timers[t] = map[int64]bool{}
				}
				if f, ok := ms.(float64); ok {
					di.Timers[t][int64(f)] = true
				}
			}
		}
	}
	walk("bot", d.Bot)
	return di, nil
}

// TimerNames returns the timer names of a document (sorted).
func (d *DocInfo) TimerNames() []string {
	var out []string
	for t := range d.Timers {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

// Action of an engine reply.
type Action struct {
	Kind  string // send | call | start_timer | cancel_timer
	Name  string
	Ms    int64
	Token int64
	Raw   json.RawMessage
}

// Reply is a validated engine reply.
type Reply struct {
	Kind      string // result | need | error
	SnapText  []byte // compact snapshot JSON as produced by the engine
	Snap      *Snapshot
	Actions   []Action
	ActsText  []byte
	Recovered bool
	Need      []string
	Err       string
}

// EngineFailure is a transport/protocol failure: safe to retry (engine is pure).
type EngineFailure struct{ Msg string }

func (e *EngineFailure) Error() string { return "engine failure: " + e.Msg }

// Faults: deterministic engine-reply faults injected between the binding and the
// host (FFI mapping of the corpus engine_fault lines, see README of host-go).
type Faults struct {
	mu       sync.Mutex
	armed    []string
	Injected map[string]int
	Detected map[string]int
	OnKill   func() // real kill -9 of this process (engine crash == host crash with FFI)
	OnEvent  func(kind, mode string) // durable record (host_log) of injections/detections
}

func NewFaults() *Faults {
	return &Faults{Injected: map[string]int{}, Detected: map[string]int{}}
}

func (f *Faults) Arm(mode string) {
	f.mu.Lock()
	f.armed = append(f.armed, mode)
	f.mu.Unlock()
}

func (f *Faults) take() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.armed) == 0 {
		return ""
	}
	m := f.armed[0]
	f.armed = f.armed[1:]
	f.Injected[m]++
	if f.OnEvent != nil {
		f.OnEvent("engine_fault_injected", m)
	}
	return m
}

func (f *Faults) detected(mode string) {
	if mode == "" {
		return
	}
	f.mu.Lock()
	f.Detected[mode]++
	f.mu.Unlock()
	if f.OnEvent != nil {
		f.OnEvent("engine_fault_detected", mode)
	}
}

func (f *Faults) Snapshot() (map[string]int, map[string]int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, b := map[string]int{}, map[string]int{}
	for k, v := range f.Injected {
		a[k] = v
	}
	for k, v := range f.Detected {
		b[k] = v
	}
	return a, b
}

// Engines is the registry of loaded machines (one per document name).
type Engines struct {
	dir        string
	mu         sync.Mutex
	machines   map[string]*figram.Machine
	infos      map[string]*DocInfo
	nextID     atomic.Int64
	Timeout    time.Duration
	Faults     *Faults
	Validate   bool // broken variant no-reply-validate turns it off
	UnsafeDrop bool // broken variant unload-no-wait
	Calls      atomic.Int64
	Retired    atomic.Int64
	rng        *rand.Rand
	rngMu      sync.Mutex
}

func NewEngines(dir string, timeout time.Duration, faults *Faults) *Engines {
	return &Engines{dir: dir, machines: map[string]*figram.Machine{}, infos: map[string]*DocInfo{},
		Timeout: timeout, Faults: faults, Validate: true, rng: rand.New(rand.NewSource(7))}
}

func (e *Engines) DocPath(doc string) string { return filepath.Join(e.dir, doc+".bot.json") }

func (e *Engines) loadLocked(doc string) (*figram.Machine, *DocInfo, error) {
	b, err := os.ReadFile(e.DocPath(doc))
	if err != nil {
		return nil, nil, err
	}
	di, err := parseDocInfo(doc, b)
	if err != nil {
		return nil, nil, err
	}
	m, err := figram.Load(doc, b)
	if err != nil {
		return nil, nil, err
	}
	e.machines[doc] = m
	e.infos[doc] = di
	return m, di, nil
}

// Get returns the machine of a document, loading it on first use.
func (e *Engines) Get(doc string) (*figram.Machine, *DocInfo, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if m, ok := e.machines[doc]; ok {
		return m, e.infos[doc], nil
	}
	return e.loadLocked(doc)
}

// Info returns DocInfo (loading the machine if needed).
func (e *Engines) Info(doc string) (*DocInfo, error) {
	_, di, err := e.Get(doc)
	return di, err
}

// Reload loads a fresh machine for doc, swaps it in and retires the old one after
// its in-flight calls finished (Close waits). Returns the load time.
func (e *Engines) Reload(doc string) (time.Duration, error) {
	b, err := os.ReadFile(e.DocPath(doc))
	if err != nil {
		return 0, err
	}
	di, err := parseDocInfo(doc, b)
	if err != nil {
		return 0, err
	}
	m, err := figram.Load(doc, b)
	if err != nil {
		return 0, err
	}
	e.mu.Lock()
	old := e.machines[doc]
	e.machines[doc] = m
	e.infos[doc] = di
	e.mu.Unlock()
	if old != nil {
		e.retire(old)
	}
	return m.LoadTime, nil
}

// Drop retires the machine of a document that no user references any more.
func (e *Engines) Drop(doc string) {
	e.mu.Lock()
	old := e.machines[doc]
	delete(e.machines, doc)
	e.mu.Unlock()
	if old != nil {
		e.retire(old)
	}
}

func (e *Engines) retire(m *figram.Machine) {
	e.Retired.Add(1)
	if e.UnsafeDrop {
		m.CloseUnsafeNoWait() // BROKEN: frees the automaton under in-flight / racing calls
		return
	}
	go m.Close() // waits for in-flight calls, then figram_unload
}

// CloseAll unloads every machine (graceful stop).
func (e *Engines) CloseAll() {
	e.mu.Lock()
	ms := e.machines
	e.machines = map[string]*figram.Machine{}
	e.mu.Unlock()
	for _, m := range ms {
		m.Close()
	}
}

type callResult struct {
	b   []byte
	err error
}

// Call sends one request (op + fields) to the machine of doc and validates the reply.
// ctxSeq is the seq of the input snapshot (or -1 for start), used for seq checks.
func (e *Engines) Call(doc string, req map[string]any, inSeq int64) (*Reply, error) {
	id := e.nextID.Add(1)
	req["id"] = id
	line, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		m, di, err := e.Get(doc)
		if err != nil {
			return nil, &EngineFailure{"load " + doc + ": " + err.Error()}
		}
		mode := e.Faults.take()
		if mode == "kill" && e.Faults.OnKill != nil {
			e.Faults.OnKill() // the embedded engine crashes = the host process dies
		}
		ch := make(chan callResult, 1)
		go func() {
			if mode == "hang" {
				time.Sleep(e.Timeout + 2*time.Second) // simulated hang before entering C
			}
			var b []byte
			var err error
			if e.UnsafeDrop {
				b, err = m.RequestUnsafe(line)
			} else {
				b, err = m.Request(line)
			}
			ch <- callResult{b, err}
		}()
		var res callResult
		select {
		case res = <-ch:
		case <-time.After(e.Timeout):
			e.Faults.detected(mode)
			return nil, &EngineFailure{fmt.Sprintf("no reply within %v", e.Timeout)}
		}
		e.Calls.Add(1)
		if errors.Is(res.err, figram.ErrClosed) {
			continue // machine was replaced by an upgrade/reload: take the new one
		}
		if res.err != nil {
			return nil, &EngineFailure{res.err.Error()}
		}
		b := e.corrupt(mode, res.b)
		r, verr := e.parseReply(b, id, di)
		if verr != nil {
			e.Faults.detected(mode)
			return nil, &EngineFailure{"bad reply: " + verr.Error()}
		}
		if mode != "" && e.Validate {
			// a fault that the validator did not notice would be silent corruption
			if !bytes.Equal(b, res.b) {
				e.Faults.detected(mode + "_undetected")
			}
		}
		return r, nil
	}
	return nil, &EngineFailure{"machine closed repeatedly"}
}

func (e *Engines) corrupt(mode string, b []byte) []byte {
	switch mode {
	case "", "kill", "hang":
		return b
	case "corrupt": // schema-valid but wrong: flip the case of the first letter of "at"
		i := bytes.Index(b, []byte(`"at":"`))
		if i < 0 {
			return append([]byte{}, b[:len(b)/2]...)
		}
		out := append([]byte{}, b...)
		j := i + len(`"at":"`)
		if out[j] >= 'a' && out[j] <= 'z' {
			out[j] -= 32
		} else {
			out[j] = 'Z'
		}
		return out
	case "wrong_id":
		var m map[string]json.RawMessage
		if json.Unmarshal(b, &m) == nil {
			m["id"] = json.RawMessage(`999999999`)
			out, _ := json.Marshal(m)
			return out
		}
		return b
	case "truncate":
		return append([]byte{}, b[:len(b)/2]...)
	case "garbage":
		e.rngMu.Lock()
		defer e.rngMu.Unlock()
		out := make([]byte, 40)
		for i := range out {
			out[i] = byte(e.rng.Intn(256))
		}
		return out
	case "bad_snapshot":
		var m map[string]json.RawMessage
		if json.Unmarshal(b, &m) == nil {
			if _, ok := m["snapshot"]; ok {
				m["snapshot"] = json.RawMessage(`{"at":"","seq":-1}`)
			}
			out, _ := json.Marshal(m)
			return out
		}
		return b
	case "bad_action":
		var m map[string]json.RawMessage
		if json.Unmarshal(b, &m) == nil {
			if a, ok := m["actions"]; ok {
				var arr []json.RawMessage
				json.Unmarshal(a, &arr)
				arr = append(arr, json.RawMessage(`{"launch":"rocket"}`))
				m["actions"], _ = json.Marshal(arr)
			}
			out, _ := json.Marshal(m)
			return out
		}
		return b
	}
	return b
}

func (e *Engines) parseReply(b []byte, id int64, di *DocInfo) (*Reply, error) {
	v, err := ParseStrict(b)
	if err != nil {
		return nil, err
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, errors.New("reply is not an object")
	}
	if !e.Validate {
		return e.parseLoose(obj)
	}
	gotID, ok := AsUint53(obj["id"])
	if !ok || gotID != id {
		return nil, fmt.Errorf("reply id %v != request id %d", obj["id"], id)
	}
	_, hasSnap := obj["snapshot"]
	_, hasNeed := obj["need"]
	_, hasErr := obj["error"]
	n := 0
	for _, h := range []bool{hasSnap, hasNeed, hasErr} {
		if h {
			n++
		}
	}
	if n != 1 {
		return nil, errors.New("reply must have exactly one of snapshot/need/error")
	}
	r := &Reply{}
	for k := range obj {
		switch k {
		case "id", "snapshot", "actions", "recovered", "need", "error":
		default:
			return nil, fmt.Errorf("unknown reply field %q", k)
		}
	}
	switch {
	case hasErr:
		s, ok := obj["error"].(string)
		if !ok {
			return nil, errors.New("error is not a string")
		}
		r.Kind, r.Err = "error", s
		return r, nil
	case hasNeed:
		arr, ok := obj["need"].([]any)
		if !ok || len(arr) == 0 {
			return nil, errors.New("need must be a non-empty array")
		}
		for _, x := range arr {
			s, ok := x.(string)
			if !ok || s == "" {
				return nil, errors.New("need entry is not a string")
			}
			r.Need = append(r.Need, s)
		}
		r.Kind = "need"
		return r, nil
	}
	snap, err := ValidateSnapshot(obj["snapshot"])
	if err != nil {
		return nil, err
	}
	if di != nil && !di.Messages[snap.At] {
		return nil, fmt.Errorf("snapshot at %q is not a message of %s", snap.At, di.Name)
	}
	// NOTE: snapshot.timers is NOT checked against the document: after a document
	// upgrade the engine legitimately keeps timers of the old version (ET-02); a
	// check here turned every later step of such users into a poison loop.
	r.Snap = snap
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		return nil, err
	}
	r.SnapText = []byte(raw["snapshot"])
	if rec, ok := obj["recovered"]; ok {
		if rec != true {
			return nil, errors.New("recovered must be true when present")
		}
		r.Recovered = true
	}
	arr, ok := obj["actions"].([]any)
	if !ok {
		return nil, errors.New("actions must be an array")
	}
	for _, x := range arr {
		a, err := parseAction(x, di)
		if err != nil {
			return nil, err
		}
		r.Actions = append(r.Actions, a)
	}
	r.ActsText = []byte(raw["actions"])
	r.Kind = "result"
	return r, nil
}

func parseAction(x any, di *DocInfo) (Action, error) {
	m, ok := x.(map[string]any)
	if !ok {
		return Action{}, errors.New("action is not an object")
	}
	raw, _ := json.Marshal(m)
	str := func(k string) (string, bool) { s, ok := m[k].(string); return s, ok && s != "" }
	switch {
	case len(m) == 1 && m["send"] != nil:
		s, ok := str("send")
		if !ok || (di != nil && !di.Messages[s]) {
			return Action{}, fmt.Errorf("bad send %v", m["send"])
		}
		return Action{Kind: "send", Name: s, Raw: raw}, nil
	case len(m) == 1 && m["call"] != nil:
		s, ok := str("call")
		if !ok || (di != nil && !di.Calls[s]) {
			return Action{}, fmt.Errorf("bad call %v", m["call"])
		}
		return Action{Kind: "call", Name: s, Raw: raw}, nil
	case len(m) == 1 && m["cancel_timer"] != nil:
		s, ok := str("cancel_timer")
		if !ok {
			return Action{}, errors.New("bad cancel_timer")
		}
		return Action{Kind: "cancel_timer", Name: s, Raw: raw}, nil
	case len(m) == 3 && m["start_timer"] != nil:
		s, ok := str("start_timer")
		ms, ok2 := AsUint53(m["ms"])
		tok, ok3 := AsUint53(m["token"])
		if !ok || !ok2 || !ok3 || (di != nil && (di.Timers[s] == nil || !di.Timers[s][ms])) {
			return Action{}, fmt.Errorf("bad start_timer %v", m)
		}
		return Action{Kind: "start_timer", Name: s, Ms: ms, Token: tok, Raw: raw}, nil
	}
	return Action{}, fmt.Errorf("unknown action %v", m)
}

// parseLoose: broken variant no-reply-validate — trust whatever the engine says.
func (e *Engines) parseLoose(obj map[string]any) (*Reply, error) {
	r := &Reply{}
	if s, ok := obj["error"].(string); ok {
		r.Kind, r.Err = "error", s
		return r, nil
	}
	if arr, ok := obj["need"].([]any); ok {
		for _, x := range arr {
			s, _ := x.(string)
			r.Need = append(r.Need, s)
		}
		r.Kind = "need"
		return r, nil
	}
	sm, _ := obj["snapshot"].(map[string]any)
	s := &Snapshot{Resume: map[string]string{}, Timers: map[string]int64{}}
	s.At, _ = sm["at"].(string)
	if n, ok := AsUint53(sm["seq"]); ok {
		s.Seq = n
	}
	r.Snap = s
	r.SnapText, _ = json.Marshal(obj["snapshot"])
	r.Recovered = obj["recovered"] == true
	arr, _ := obj["actions"].([]any)
	for _, x := range arr {
		a, err := parseAction(x, nil)
		if err != nil {
			continue
		}
		r.Actions = append(r.Actions, a)
	}
	r.ActsText, _ = json.Marshal(obj["actions"])
	r.Kind = "result"
	return r, nil
}
