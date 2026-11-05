//go:build cgo

package figram

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// The typed engine protocol, version 3 (docs/format.md, section 8, in the Figram repository).
// Every reply is checked strictly: the id echo, exactly one reply variant, the field types and
// value ranges; a reply that breaks the protocol is an error, never a partial result.

// MaxInt is the largest integer of the protocol (2^53 − 1): tokens, delays and seq.
const MaxInt = 1<<53 - 1

// ProtocolVersion is the version of the engine protocol this package speaks; [Load] rejects a
// library that speaks another one (possible with the build tag figram_system).
const ProtocolVersion = 3

// Snapshot is the state of one user, kept by the host between events (in a database, for
// example) and passed back with the next request. Store it as its JSON encoding; the engine
// rejects unknown fields.
type Snapshot struct {
	V       int               `json:"v,omitempty"`       // version; 0 means 1
	At      string            `json:"at"`                // the current message
	Back    string            `json:"back,omitempty"`    // the message "back" leads to (left at the last section crossing)
	Resume  map[string]string `json:"resume,omitempty"`  // section → message: the places "resume" leads to
	Timers  map[string]uint64 `json:"timers,omitempty"`  // timer → token: the active timers
	Pending []Wait            `json:"pending,omitempty"` // the calls whose outcome is awaited, newest first
	Seq     uint64            `json:"seq,omitempty"`     // the number of the last transition
}

// Wait is a call of an action with outcomes whose outcome has not come yet: [Bot.Outcome] with
// this action and token is accepted once.
type Wait struct {
	Call  string `json:"call"`  // the action
	Token uint64 `json:"token"` // the token of the call
}

// ActionKind is the kind of an [Action].
type ActionKind string

// The actions the host executes, in the order of [Result.Actions].
const (
	Send        ActionKind = "send"         // send the message Name to the user
	Call        ActionKind = "call"         // run the action Name of the host (see Outcomes, Detached)
	StartTimer  ActionKind = "start_timer"  // start the timer Name for Ms milliseconds with Token
	CancelTimer ActionKind = "cancel_timer" // cancel the timer Name
)

// Action is one action of a transition.
type Action struct {
	Kind  ActionKind
	Name  string // the message (Send), the action (Call) or the timer (StartTimer, CancelTimer)
	Ms    uint64 // StartTimer: the delay in milliseconds
	Token uint64 // StartTimer: the token to pass to [Bot.Timer]; Call with Outcomes: to [Bot.Outcome]
	// Outcomes (Call): the action reports how it ended. After performing it, deliver exactly one
	// outcome with [Bot.Outcome] and Token: a declared outcome, or "failed" when the host gives
	// up without an answer (reconcile first: docs/host-contract.md, section 2).
	Outcomes bool
	// Detached (Call): a background action. Run it later and in any order; the user's next event
	// does not wait for it.
	Detached bool
}

// MarshalJSON writes the protocol form, e.g. {"start_timer":"t","ms":5000,"token":3}.
func (a Action) MarshalJSON() ([]byte, error) {
	switch a.Kind {
	case Send, CancelTimer:
		return json.Marshal(map[string]string{string(a.Kind): a.Name})
	case Call:
		c := struct {
			Name     string  `json:"call"`
			Token    *uint64 `json:"token,omitempty"`
			Detached bool    `json:"detached,omitempty"`
		}{Name: a.Name, Detached: a.Detached}
		if a.Outcomes {
			c.Token = &a.Token
		}
		return json.Marshal(c)
	case StartTimer:
		return json.Marshal(struct {
			Name  string `json:"start_timer"`
			Ms    uint64 `json:"ms"`
			Token uint64 `json:"token"`
		}{a.Name, a.Ms, a.Token})
	}
	return nil, fmt.Errorf("figram: unknown action kind %q", a.Kind)
}

// UnmarshalJSON reads the protocol form strictly.
func (a *Action) UnmarshalJSON(b []byte) error {
	v, err := decodeJSON(b)
	if err != nil {
		return err
	}
	if err := checkAction(v); err != nil {
		return err
	}
	*a = toAction(v.(map[string]any))
	return nil
}

// Result is the reply to [Bot.Start], [Bot.StartWith], [Bot.Restart], [Bot.Step] and [Bot.Timer].
type Result struct {
	// Need is set only by a step or a start with an event whose choice depends on conditions
	// that were not given: repeat the same call with their values added to the guards (see
	// [Decide]). Snapshot and Actions are empty then.
	Need []string
	// Snapshot is the new state of the user: store it, then execute Actions.
	Snapshot Snapshot
	// Actions are executed by the host in this order.
	Actions []Action
	// Recovered: the snapshot's message is not in the document (it was edited). The old timers
	// were cancelled, the remembered places kept, and the event was processed as [Bot.StartWith]
	// processes it (a timer event starts the user over). Do not deliver the event again: it has
	// been processed.
	Recovered bool
}

// MarshalJSON writes the protocol form of the reply: {"need": [...]} or {"snapshot": ...,
// "actions": [...]} with "recovered": true when set.
func (r Result) MarshalJSON() ([]byte, error) {
	if len(r.Need) > 0 {
		return json.Marshal(map[string]any{"need": r.Need})
	}
	m := map[string]any{"snapshot": r.Snapshot, "actions": r.Actions}
	if r.Actions == nil {
		m["actions"] = []Action{}
	}
	if r.Recovered {
		m["recovered"] = true
	}
	return json.Marshal(m)
}

// Hello is the reply to [Bot.Hello].
type Hello struct {
	Protocol int // the protocol version: [ProtocolVersion]
	States   int // the number of messages of the document
}

// The error codes of the protocol; hosts decide by the code, not by the text.
const (
	CodeParseError  = "parse_error"  // the request is not JSON, not UTF-8, or too deep
	CodeTooLarge    = "too_large"    // the request is longer than the limit ([SetMaxRequest])
	CodeBadRequest  = "bad_request"  // a wrong field: an empty event, a token without a timer event, ...
	CodeBadSnapshot = "bad_snapshot" // the snapshot is not accepted
	CodeSeqOverflow = "seq_overflow" // the new seq would exceed MaxInt
	// CodeSnapshotTooLarge: the new snapshot would take more than 15/16 of the request size limit
	// ([SetMaxRequest]); the stored snapshot still works for the outcomes and [Bot.Restart].
	CodeSnapshotTooLarge = "snapshot_too_large"
)

var errorCodes = map[string]bool{
	CodeParseError: true, CodeTooLarge: true, CodeBadRequest: true, CodeBadSnapshot: true, CodeSeqOverflow: true,
	CodeSnapshotTooLarge: true,
}

// ProtocolError is an error reply of the engine. The snapshot of the request is unchanged, and
// repeating the same request gives the same error.
type ProtocolError struct {
	Code    string // one of the Code constants
	Message string // for humans; it may change between versions
}

func (e *ProtocolError) Error() string { return "figram: " + e.Code + ": " + e.Message }

// Hello returns the protocol version and the number of messages of the document.
func (b *Bot) Hello() (Hello, error) {
	r, err := b.call("hello", nil)
	if err != nil {
		return Hello{}, err
	}
	return Hello{Protocol: toInt(r["protocol"]), States: toInt(r["states"])}, nil
}

// Start enters the bot for a new user. seq is the user's high-water mark: the largest seq ever
// issued to this user, or 0 for a user who never had a snapshot. Starting an existing user over
// from 0 gives new actions the idempotency keys of old ones (docs/host-contract.md); use
// [Bot.Restart] with the user's snapshot instead. A user who comes with an update (a command, a
// button of an old message) is started with [Bot.StartWith] and its event.
func (b *Bot) Start(seq uint64) (*Result, error) {
	return b.result("start", startFields(seq))
}

// StartWith enters the bot for a new user with the user's first event, for example "cmd:start"
// or the button of an old message. The event is matched as if the user stood on the initial
// message, which is neither entered nor left: a matching variant gives its actions and the entry
// into its target in one result; without one the result is that of [Bot.Start]. guards and Need
// work as for [Bot.Step] (see [Decide]); seq is the high-water mark as for [Bot.Start]. A timer
// event is rejected with [CodeBadRequest].
func (b *Bot) StartWith(seq uint64, event string, guards map[string]bool) (*Result, error) {
	f := startFields(seq)
	f["event"] = event
	if len(guards) > 0 {
		f["guards"] = guards
	}
	return b.result("start", f)
}

func startFields(seq uint64) map[string]any {
	f := map[string]any{}
	if seq != 0 {
		f["seq"] = seq
	}
	return f
}

// Restart starts the user over: it cancels the snapshot's timers, clears the remembered places
// and enters the bot again, continuing the seq counter.
func (b *Bot) Restart(s Snapshot) (*Result, error) {
	return b.result("restart", map[string]any{"snapshot": s})
}

// Step processes the event (for example "btn:order") for the user in snapshot s. guards holds
// the values of conditions the host already knows; nil is allowed. If the choice depends on
// other conditions, the result has only Need set (see [Decide]).
func (b *Bot) Step(s Snapshot, event string, guards map[string]bool) (*Result, error) {
	f := map[string]any{"snapshot": s, "event": event}
	if len(guards) > 0 {
		f["guards"] = guards
	}
	return b.result("step", f)
}

// Timer processes the firing of a timer started by a [StartTimer] action: timer is its Name and
// token its Token. A firing with a stale token, or after the user left the message, changes
// nothing (the result has the same snapshot and no actions).
func (b *Bot) Timer(s Snapshot, timer string, token uint64, guards map[string]bool) (*Result, error) {
	f := map[string]any{"snapshot": s, "event": "timer:" + timer, "token": token}
	if len(guards) > 0 {
		f["guards"] = guards
	}
	return b.result("step", f)
}

// Outcome processes the outcome of a call of an action with outcomes ([Action.Outcomes]): action
// is its Name, outcome a declared outcome of it or "failed", token its Token. The engine accepts
// it once, only while the snapshot waits for that call ([Snapshot.Pending]), and then handles it
// like any event, by the arrows of the message the user is on; a stale or repeated outcome
// changes nothing. guards and Need work as for [Bot.Step].
func (b *Bot) Outcome(s Snapshot, action, outcome string, token uint64, guards map[string]bool) (*Result, error) {
	if action == "" || outcome == "" || strings.Contains(outcome, ":") {
		return nil, fmt.Errorf("figram: outcome event: action %q, outcome %q (both non-empty, no ':' in the outcome)",
			action, outcome)
	}
	f := map[string]any{"snapshot": s, "event": "call:" + action + ":" + outcome, "token": token}
	if len(guards) > 0 {
		f["guards"] = guards
	}
	return b.result("step", f)
}

// Decide runs step until it returns a result without Need, evaluating every needed condition
// once with eval and passing the values collected so far to the next call of step. guards are
// the values known in advance (they are not modified). For example:
//
//	res, err := figram.Decide(nil, conditionHolds, func(g map[string]bool) (*figram.Result, error) {
//		return bot.Step(snap, "btn:checkout", g)
//	})
func Decide(guards map[string]bool, eval func(condition string) (bool, error),
	step func(guards map[string]bool) (*Result, error)) (*Result, error) {
	known := make(map[string]bool, len(guards))
	for k, v := range guards {
		known[k] = v
	}
	for {
		r, err := step(known)
		if err != nil || len(r.Need) == 0 {
			return r, err
		}
		for _, c := range r.Need {
			if _, ok := known[c]; ok {
				// The engine never asks again for a given condition: this would loop forever.
				return nil, fmt.Errorf("figram: the engine asked again for condition %q", c)
			}
			v, err := eval(c)
			if err != nil {
				return nil, err
			}
			known[c] = v
		}
	}
}

func (b *Bot) result(op string, fields map[string]any) (*Result, error) {
	r, err := b.call(op, fields)
	if err != nil {
		return nil, err
	}
	if need, ok := r["need"]; ok {
		res := &Result{}
		for _, c := range need.([]any) {
			res.Need = append(res.Need, c.(string))
		}
		return res, nil
	}
	res := &Result{Snapshot: toSnapshot(r["snapshot"].(map[string]any)), Recovered: r["recovered"] == true}
	for _, a := range r["actions"].([]any) {
		res.Actions = append(res.Actions, toAction(a.(map[string]any)))
	}
	return res, nil
}

// call sends op with fields and a fresh id and returns the checked reply without the id, or the
// error reply as a *ProtocolError.
func (b *Bot) call(op string, fields map[string]any) (map[string]any, error) {
	id := b.nextID.Add(1)
	msg := make(map[string]any, len(fields)+2)
	for k, v := range fields {
		msg[k] = v
	}
	msg["op"] = op
	msg["id"] = id
	line, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("figram: request cannot be encoded: %w", err)
	}
	out, err := b.Request(line)
	if err != nil {
		return nil, err
	}
	v, err := decodeJSON(out)
	if err != nil {
		return nil, fmt.Errorf("figram: reply is not JSON: %w", err)
	}
	r, ok := v.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("figram: reply is not an object: %.200s", out)
	}
	variant, err := checkReply(r, id, op)
	if err != nil {
		return nil, fmt.Errorf("figram: protocol violation in the reply to %s: %w", op, err)
	}
	if variant == "error" {
		return nil, &ProtocolError{Code: r["code"].(string), Message: r["error"].(string)}
	}
	delete(r, "id")
	return r, nil
}

func decodeJSON(b []byte) (any, error) {
	var v any
	dec := json.NewDecoder(strings.NewReader(string(b)))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, errors.New("text after the JSON value")
	}
	return v, nil
}

func natOK(v any) bool {
	n, ok := v.(json.Number)
	if !ok {
		return false
	}
	i, err := strconv.ParseInt(string(n), 10, 64)
	return err == nil && i >= 0 && i <= MaxInt && string(n) == strconv.FormatInt(i, 10)
}

func nameOK(v any) bool { s, ok := v.(string); return ok && s != "" }

func keysAre(m map[string]any, keys ...string) bool {
	if len(m) != len(keys) {
		return false
	}
	for _, k := range keys {
		if _, ok := m[k]; !ok {
			return false
		}
	}
	return true
}

func idIs(v any, id int64) bool {
	return natOK(v) && v.(json.Number) == json.Number(strconv.FormatInt(id, 10))
}

// checkSnapshot checks a snapshot as schemas/snapshot.schema.json does.
func checkSnapshot(v any) error {
	s, ok := v.(map[string]any)
	if !ok {
		return fmt.Errorf("snapshot is not an object: %v", v)
	}
	for k := range s {
		switch k {
		case "v", "at", "back", "resume", "timers", "pending", "seq":
		default:
			return fmt.Errorf("snapshot: unknown field %q", k)
		}
	}
	if v, ok := s["v"]; ok && (!natOK(v) || v.(json.Number) != "1") {
		return fmt.Errorf("snapshot: version %v", v)
	}
	if !nameOK(s["at"]) {
		return fmt.Errorf("snapshot.at: %v", s["at"])
	}
	if v, ok := s["back"]; ok && !nameOK(v) {
		return fmt.Errorf("snapshot.back: %v", v)
	}
	if r, ok := s["resume"]; ok {
		m, ok := r.(map[string]any)
		if !ok {
			return fmt.Errorf("snapshot.resume is not an object")
		}
		for k, v := range m {
			if k == "" || !nameOK(v) {
				return fmt.Errorf("snapshot.resume.%q: %v", k, v)
			}
		}
	}
	if t, ok := s["timers"]; ok {
		m, ok := t.(map[string]any)
		if !ok {
			return fmt.Errorf("snapshot.timers is not an object")
		}
		for k, v := range m {
			if k == "" || !natOK(v) {
				return fmt.Errorf("snapshot.timers.%q: %v", k, v)
			}
		}
	}
	if p, ok := s["pending"]; ok {
		l, ok := p.([]any)
		if !ok {
			return fmt.Errorf("snapshot.pending is not a list")
		}
		for _, w := range l {
			m, ok := w.(map[string]any)
			if !ok || !keysAre(m, "call", "token") || !nameOK(m["call"]) || !natOK(m["token"]) {
				return fmt.Errorf("snapshot.pending: %v", w)
			}
		}
	}
	if q, ok := s["seq"]; ok && !natOK(q) {
		return fmt.Errorf("snapshot.seq: %v", q)
	}
	return nil
}

func checkAction(a any) error {
	m, ok := a.(map[string]any)
	if !ok {
		return fmt.Errorf("action is not an object: %v", a)
	}
	switch {
	case keysAre(m, "send"), keysAre(m, "cancel_timer"):
		for _, x := range m {
			if !nameOK(x) {
				return fmt.Errorf("action: %v", m)
			}
		}
	case keysAre(m, "call"), keysAre(m, "call", "token"), keysAre(m, "call", "detached"),
		keysAre(m, "call", "token", "detached"):
		// token: an action with outcomes; detached: a background action, written only as true
		if !nameOK(m["call"]) {
			return fmt.Errorf("call: %v", m)
		}
		if t, ok := m["token"]; ok && !natOK(t) {
			return fmt.Errorf("call.token: %v", m)
		}
		if d, ok := m["detached"]; ok && d != true {
			return fmt.Errorf("call.detached: %v", m)
		}
	case keysAre(m, "start_timer", "ms", "token"):
		if !nameOK(m["start_timer"]) || !natOK(m["ms"]) || !natOK(m["token"]) {
			return fmt.Errorf("start_timer: %v", m)
		}
	default:
		return fmt.Errorf("unknown action form: %v", m)
	}
	return nil
}

func checkActions(v any) error {
	acts, ok := v.([]any)
	if !ok {
		return fmt.Errorf("actions is not a list: %v", v)
	}
	for _, a := range acts {
		if err := checkAction(a); err != nil {
			return err
		}
	}
	return nil
}

// checkReply accepts exactly one reply variant to op: "result", "need", "hello" or "error".
func checkReply(r map[string]any, id int64, op string) (string, error) {
	rest := map[string]any{}
	for k, v := range r {
		if k != "id" {
			rest[k] = v
		}
	}
	if _, isErr := rest["error"]; isErr {
		if !keysAre(rest, "error", "code") {
			return "", fmt.Errorf("error reply: fields %v (expected id, error, code)", rest)
		}
		if _, ok := rest["error"].(string); !ok {
			return "", fmt.Errorf("error is not a string")
		}
		code, ok := rest["code"].(string)
		if !ok || !errorCodes[code] {
			return "", fmt.Errorf("unknown error code: %v", rest["code"])
		}
		// id null only for failures detected before the request was read (the library detects
		// parse_error and too_large itself); otherwise the id must be the request's.
		if idv, has := r["id"]; has && idv == nil && (code == CodeParseError || code == CodeTooLarge) {
			return "error", nil
		}
		if !idIs(r["id"], id) {
			return "", fmt.Errorf("reply id %v instead of %d", r["id"], id)
		}
		return "error", nil
	}
	if !idIs(r["id"], id) {
		return "", fmt.Errorf("reply id %v instead of %d", r["id"], id)
	}
	if op == "hello" {
		if !keysAre(rest, "protocol", "states") || !natOK(rest["protocol"]) || !natOK(rest["states"]) {
			return "", fmt.Errorf("hello: %v", rest)
		}
		return "hello", nil
	}
	if (op == "step" || op == "start") && keysAre(rest, "need") {
		need, ok := rest["need"].([]any)
		if !ok || len(need) == 0 {
			return "", fmt.Errorf("need: %v", rest["need"])
		}
		for _, n := range need {
			if !nameOK(n) {
				return "", fmt.Errorf("need: %v", n)
			}
		}
		return "need", nil
	}
	if !keysAre(rest, "snapshot", "actions") && !keysAre(rest, "snapshot", "actions", "recovered") {
		return "", fmt.Errorf("reply to %s: fields %v", op, rest)
	}
	if rec, ok := rest["recovered"]; ok && (rec != true || op != "step") {
		return "", fmt.Errorf("recovered: %v", rec)
	}
	if err := checkSnapshot(rest["snapshot"]); err != nil {
		return "", err
	}
	if err := checkActions(rest["actions"]); err != nil {
		return "", err
	}
	return "result", nil
}

// The conversions below take values already accepted by checkReply.

func toInt(v any) int { i, _ := strconv.ParseInt(string(v.(json.Number)), 10, 64); return int(i) }

func toUint(v any) uint64 { i, _ := strconv.ParseUint(string(v.(json.Number)), 10, 64); return i }

func toSnapshot(m map[string]any) Snapshot {
	s := Snapshot{At: m["at"].(string)}
	if v, ok := m["v"]; ok {
		s.V = toInt(v)
	}
	if v, ok := m["back"]; ok {
		s.Back = v.(string)
	}
	if r, ok := m["resume"].(map[string]any); ok {
		s.Resume = make(map[string]string, len(r))
		for k, v := range r {
			s.Resume[k] = v.(string)
		}
	}
	if t, ok := m["timers"].(map[string]any); ok {
		s.Timers = make(map[string]uint64, len(t))
		for k, v := range t {
			s.Timers[k] = toUint(v)
		}
	}
	if p, ok := m["pending"].([]any); ok {
		s.Pending = make([]Wait, 0, len(p))
		for _, w := range p {
			wm := w.(map[string]any)
			s.Pending = append(s.Pending, Wait{Call: wm["call"].(string), Token: toUint(wm["token"])})
		}
	}
	if q, ok := m["seq"]; ok {
		s.Seq = toUint(q)
	}
	return s
}

func toAction(m map[string]any) Action {
	if name, ok := m["start_timer"]; ok {
		return Action{Kind: StartTimer, Name: name.(string), Ms: toUint(m["ms"]), Token: toUint(m["token"])}
	}
	if name, ok := m["call"]; ok {
		a := Action{Kind: Call, Name: name.(string), Detached: m["detached"] == true}
		if t, ok := m["token"]; ok {
			a.Outcomes, a.Token = true, toUint(t)
		}
		return a
	}
	for k, v := range m {
		return Action{Kind: ActionKind(k), Name: v.(string)}
	}
	return Action{}
}
