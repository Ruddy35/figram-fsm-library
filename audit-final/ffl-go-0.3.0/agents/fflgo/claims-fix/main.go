// auditclaims: checks of ffl-go's documented claims through its public API, each with a control
// case where the claim holds. Usage: auditclaims claims | limit DOC EVENT LIMIT [ENGINE_LIMIT]
//
// limit: press EVENT until ffl-go refuses the step under SetMaxRequest(LIMIT); every request is
// also sent, in ffl-go's encoding, to `figram-engine --max-request ENGINE_LIMIT` (same request)
// and, in the engine's own encoding, along the engine's own chain. ENGINE_LIMIT other than LIMIT
// is the negative control.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"reflect"
	"strconv"

	figram "github.com/Ruddy35/ffl-go"
)

const ex = "/home/user/figram-audit-worktrees/cleanup-int/examples/"
const engineBin = "/home/user/figram-audit-worktrees/cleanup-int/.lake/build/bin/figram-engine"

var failed int

func check(name string, holds bool, detail string) {
	v := "HOLDS"
	if !holds {
		v = "DOES NOT HOLD"
	}
	fmt.Printf("[%s] %s\n      %s\n", v, name, detail)
}

func load(p string) *figram.Bot {
	b, err := os.ReadFile(p)
	if err != nil {
		panic(err)
	}
	bot, err := figram.Load(string(b))
	if err != nil {
		panic(err)
	}
	return bot
}

func js(v any) string { b, _ := json.Marshal(v); return string(b) }

func code(err error) string {
	var pe *figram.ProtocolError
	if errors.As(err, &pe) {
		return pe.Code
	}
	if err != nil {
		return "non-protocol error: " + err.Error()
	}
	return "ok"
}

func unchanged(in figram.Snapshot, r *figram.Result, err error) bool {
	return err == nil && reflect.DeepEqual(r.Snapshot, in) && len(r.Actions) == 0 && !r.Recovered
}

func claims() {
	pizza := load(ex + "pizza.bot.json")
	// C1 Outcome: "a stale or repeated outcome changes nothing" (protocol.go Outcome, README, CHANGELOG).
	placing := figram.Snapshot{At: "placing", Resume: map[string]string{"order": "placing"},
		Pending: []figram.Wait{{Call: "create_order", Token: 6}}, Seq: 6}
	done, _ := pizza.Outcome(placing, "create_order", "created", 6, nil)
	r, err := pizza.Outcome(done.Snapshot, "create_order", "created", 6, nil)
	check("control: repeated outcome on a snapshot of the current document changes nothing", unchanged(done.Snapshot, r, err), js(r))
	r, err = pizza.Outcome(placing, "create_order", "created", 5, nil)
	check("control: stale outcome (token 5) changes nothing", unchanged(placing, r, err), js(r))
	old := done.Snapshot
	old.At = "gone" // the user's message was removed by a document update (host-contract.md, section 6)
	r, err = pizza.Outcome(old, "create_order", "created", 6, nil)
	check("C1 repeated outcome with a snapshot whose message is no longer in the document changes nothing",
		unchanged(old, r, err), "in "+js(old)+"\n      out "+js(r))
	// C2 Timer: "A firing with a stale token, or after the user left the message, changes nothing".
	cart := figram.Snapshot{At: "cart", Timers: map[string]uint64{"cart_reminder": 3}, Seq: 3}
	r, err = pizza.Timer(cart, "cart_reminder", 2, nil)
	check("control: stale timer token changes nothing", unchanged(cart, r, err), js(r))
	cart.At = "gone"
	r, err = pizza.Timer(cart, "cart_reminder", 2, nil)
	check("C2 stale timer token with a snapshot whose message is no longer in the document changes nothing",
		unchanged(cart, r, err), "in "+js(cart)+"\n      out "+js(r))
	// C3 Outcome edge cases from the brief (token 0, "failed", an action without outcomes).
	r, err = pizza.Outcome(placing, "create_order", "created", 0, nil)
	check("C3a outcome with token 0 (no wait has it) changes nothing", unchanged(placing, r, err), js(r))
	r, err = pizza.Outcome(placing, "clear_cart", "done", 6, nil)
	check("C3b outcome for an action without outcomes (no wait for it) changes nothing", unchanged(placing, r, err), js(r))
	r, err = pizza.Outcome(placing, "create_order", "failed", 6, nil)
	check("C3c outcome failed is accepted and removes the wait", err == nil && r.Snapshot.Pending == nil && r.Snapshot.At == "order_failed", js(r)+" "+code(err))
	_, err = pizza.Outcome(placing, "create_order", "a:b", 6, nil)
	var pe *figram.ProtocolError
	check("C3d a malformed outcome is refused before it is sent; the error is not a *ProtocolError (no Code to decide by)",
		err != nil && !errors.As(err, &pe), code(err))
	// C4 Restart and the waits (Restart's doc comment does not mention pending).
	r, err = pizza.Restart(placing)
	check("C4 Restart clears the waits (behaviour; the Restart doc comment lists timers and remembered places only)",
		err == nil && r.Snapshot.Pending == nil, js(r))
	// C5 the typed API and invalid UTF-8: the C ABI rejects it with parse_error, never replacing it.
	menu := figram.Snapshot{At: "menu", Seq: 1}
	raw, _ := pizza.Request([]byte("{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"seq\":1},\"event\":\"btn:\xff\"}"))
	ri, erri := pizza.Step(menu, "btn:\xff", nil)
	rr, errr := pizza.Step(menu, "btn:�", nil)
	check("C5 an event with invalid UTF-8 is rejected (C ABI: never replaced with U+FFFD)", erri != nil,
		"raw request: "+string(raw)+"\n      Step(\"btn:\\xff\"): "+js(ri)+" "+code(erri)+"\n      Step(\"btn:\\uFFFD\"): "+js(rr)+" "+code(errr))
	bad := menu
	bad.At = "men\xff"
	rb, errb := pizza.Step(bad, "btn:order", nil)
	check("C5b a snapshot whose at has invalid UTF-8 is rejected (bad_snapshot or parse_error)", errb != nil,
		"Step: "+js(rb)+" "+code(errb))
	// C6 README: CodeSnapshotTooLarge "means that the user has as many unanswered waits as the request
	// limit allows". Smallest limit at which a step without any waits is refused that way.
	ord := load("/home/user/figram-audit/agents7/fflgo/docs/order.bot.json")
	home := figram.Snapshot{At: "home", Seq: 1}
	found := false
	for l := 40; l < 400 && !found; l++ {
		prev := figram.SetMaxRequest(l)
		r, err := ord.Step(home, "btn:go", nil)
		figram.SetMaxRequest(prev)
		if code(err) == figram.CodeSnapshotTooLarge {
			check("C6 snapshot_too_large means unanswered waits", false,
				fmt.Sprintf("SetMaxRequest(%d): Step(%s, btn:go) = %s with no wait in the old or the new snapshot (the new one gains back, resume and two timers)", l, js(home), code(err)))
			found = true
		}
		_ = r
	}
	if !found {
		check("C6 snapshot_too_large means unanswered waits", true, "no limit 40..399 gives snapshot_too_large without waits")
	}
	// C7 map order: the stored snapshot (json.Marshal of res.Snapshot, as the README says) versus the
	// engine's own text, and the order of cancel_timer in Restart.
	st, _ := ord.Start(0)
	stored, _ := json.Marshal(st.Snapshot)
	line, _ := ord.Request([]byte(`{"id":1,"op":"start"}`))
	fmt.Printf("      engine reply to start:  %s\n      ffl-go stored snapshot: %s\n", line, stored)
	rs, _ := ord.Restart(st.Snapshot)
	var eng struct {
		Snapshot json.RawMessage `json:"snapshot"`
	}
	json.Unmarshal(line, &eng)
	rline, _ := ord.Request([]byte(`{"id":2,"op":"restart","snapshot":` + string(eng.Snapshot) + `}`))
	check("C7 Restart through ffl-go gives the actions the engine gives for the snapshot it wrote",
		bytes.Contains(rline, []byte(js(rs.Actions))), "ffl-go actions "+js(rs.Actions)+"\n      engine "+string(rline))
}

// ------------------------------------------------------------------ limit comparison

type eproc struct {
	w  *bufio.Writer
	in interface{ Close() error }
	r  *bufio.Reader
	id int
}

func startEngine(doc string, limit int) *eproc {
	cmd := exec.Command(engineBin, "--max-request", strconv.Itoa(limit), doc)
	in, _ := cmd.StdinPipe()
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		panic(err)
	}
	return &eproc{w: bufio.NewWriter(in), in: in, r: bufio.NewReaderSize(out, 1<<20)}
}

// do sends a request line with the given fields text (ffl-go's encoding or the engine's own) and
// returns the reply without its id.
func (e *eproc) do(fields string) []byte {
	e.id++
	fmt.Fprintf(e.w, `{"id":%d,%s}`+"\n", e.id, fields)
	e.w.Flush()
	out, err := e.r.ReadBytes('\n')
	if err != nil {
		panic(err)
	}
	p := fmt.Sprintf(`{"id":%d,`, e.id)
	return append([]byte("{"), bytes.TrimSpace(out[len(p):])...)
}

func val(b []byte) any {
	var v any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	d.Decode(&v)
	return v
}

func resJSON(r *figram.Result, err error) []byte {
	var pe *figram.ProtocolError
	if errors.As(err, &pe) {
		b, _ := json.Marshal(map[string]string{"error": pe.Message, "code": pe.Code})
		return b
	}
	if err != nil {
		panic(err)
	}
	b, _ := json.Marshal(r)
	return b
}

// fields of a request in ffl-go's encoding (json.Marshal of a map: sorted keys, HTML escaping).
func fflFields(m map[string]any) string {
	b, _ := json.Marshal(m)
	return string(b[1 : len(b)-1])
}

func limit(doc, event string, lim, elim int) {
	bot := load(doc)
	same, chain := startEngine(doc, elim), startEngine(doc, elim)
	figram.SetMaxRequest(lim)
	res, err := bot.Start(0)
	if err != nil {
		panic(err)
	}
	s := res.Snapshot
	var eng struct {
		Snapshot json.RawMessage `json:"snapshot"`
	}
	json.Unmarshal(chain.do(`"op":"start"`), &eng)
	sameEq, sameDiff, chainEq, chainDiff := 0, 0, 0, 0
	cmp := func(what string, r *figram.Result, err error, req map[string]any, chainFields string) []byte {
		got := resJSON(r, err)
		if w := same.do(fflFields(req)); reflect.DeepEqual(val(got), val(w)) {
			sameEq++
		} else {
			sameDiff++
			if sameDiff <= 3 {
				fmt.Printf("SAME-REQUEST DIFF %s\n  ffl-go %.200s\n  engine %.200s\n", what, got, w)
			}
		}
		w := chain.do(chainFields)
		if reflect.DeepEqual(val(got), val(w)) {
			chainEq++
		} else {
			chainDiff++
			fmt.Printf("CHAIN DIFF %s\n  ffl-go %.160s\n  engine %.160s\n", what, got, w)
		}
		return w
	}
	steps := 0
	for ; steps < 100000; steps++ {
		r, err := bot.Step(s, event, nil)
		cw := cmp(fmt.Sprintf("step %d", steps), r, err, map[string]any{"op": "step", "snapshot": s, "event": event},
			`"op":"step","event":`+js(event)+`,"snapshot":`+string(eng.Snapshot))
		if err != nil {
			fmt.Printf("step %d refused: %s (%d waits)\n", steps, code(err), len(s.Pending))
			break
		}
		s = r.Snapshot
		var next struct {
			Snapshot json.RawMessage `json:"snapshot"`
		}
		json.Unmarshal(cw, &next)
		eng.Snapshot = next.Snapshot
	}
	w := s.Pending[len(s.Pending)-1]
	r, err := bot.Outcome(s, w.Call, "pong", w.Token, nil)
	ev := "call:" + w.Call + ":pong"
	cmp("outcome of the oldest wait", r, err, map[string]any{"op": "step", "snapshot": s, "event": ev, "token": w.Token},
		`"op":"step","event":`+js(ev)+`,"token":`+strconv.FormatUint(w.Token, 10)+`,"snapshot":`+string(eng.Snapshot))
	fmt.Printf("Outcome with the stored snapshot: %s\n", code(err))
	r, err = bot.Restart(s)
	cmp("restart", r, err, map[string]any{"op": "restart", "snapshot": s}, `"op":"restart","snapshot":`+string(eng.Snapshot))
	fmt.Printf("Restart with the stored snapshot: %s\n", code(err))
	fmt.Printf("same request: %d equal, %d different; engine chain: %d equal, %d different\n", sameEq, sameDiff, chainEq, chainDiff)
	if sameDiff > 0 || chainDiff > 0 || sameEq == 0 {
		os.Exit(1)
	}
}

func main() {
	switch os.Args[1] {
	case "claims":
		claims()
	case "limit":
		l, _ := strconv.Atoi(os.Args[4])
		el := l
		if len(os.Args) > 5 {
			el, _ = strconv.Atoi(os.Args[5])
		}
		limit(os.Args[2], os.Args[3], l, el)
	}
	_ = failed
}
