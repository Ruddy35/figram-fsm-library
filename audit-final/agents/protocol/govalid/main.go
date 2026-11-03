// Copied verbatim from clients/go/main.go (validator functions only) by the audit.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const maxInt = 9007199254740991

// protocolVersion is the engine protocol these checks follow (docs/format.md, section 8):
// a hello reply of another version is a protocol violation.
const protocolVersion = "3"

func natOK(v any) bool {
	n, ok := v.(json.Number)
	if !ok {
		return false
	}
	i, err := strconv.ParseInt(string(n), 10, 64)
	return err == nil && i >= 0 && i <= maxInt && string(n) == strconv.FormatInt(i, 10)
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

// checkSnapshot validates by schemas/snapshot.schema.json, for the snapshots the engine writes:
// integers must be written as integers (the schema and the engine also accept 5.0 and 5e0).
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
	if b, ok := s["back"]; ok && !nameOK(b) {
		return fmt.Errorf("snapshot.back: %v", b)
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
		// waits for outcomes (docs/format.md, section 7, decision 12): exactly {call, token}
		ws, ok := p.([]any)
		if !ok {
			return fmt.Errorf("snapshot.pending is not a list")
		}
		for i, w := range ws {
			m, ok := w.(map[string]any)
			if !ok || !keysAre(m, "call", "token") || !nameOK(m["call"]) || !natOK(m["token"]) {
				return fmt.Errorf("snapshot.pending[%d]: %v", i, w)
			}
		}
	}
	if q, ok := s["seq"]; ok && !natOK(q) {
		return fmt.Errorf("snapshot.seq: %v", q)
	}
	return nil
}

func checkActions(v any) error {
	acts, ok := v.([]any)
	if !ok {
		return fmt.Errorf("actions is not a list: %v", v)
	}
	for _, a := range acts {
		m, ok := a.(map[string]any)
		if !ok {
			return fmt.Errorf("action is not an object: %v", a)
		}
		switch {
		case keysAre(m, "send"), keysAre(m, "call"), keysAre(m, "cancel_timer"):
			for _, x := range m {
				if !nameOK(x) {
					return fmt.Errorf("action: %v", m)
				}
			}
		// protocol 3: token — an action with outcomes, detached — a background action
		// (only ever written as true)
		case keysAre(m, "call", "token"), keysAre(m, "call", "detached"), keysAre(m, "call", "token", "detached"):
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
	}
	return nil
}

// Error codes of the protocol (docs/format.md, section 8, "Errors").
var errorCodes = map[string]bool{
	"parse_error": true, "too_large": true, "bad_request": true, "bad_snapshot": true, "seq_overflow": true,
}

// legacyErrorWithoutCode: an older engine replied to an error with {id, error} without code.
// This engine always adds code (docs/format.md, section 8), so the form without code is a
// protocol violation. Extra fields and unknown codes are always rejected.
const legacyErrorWithoutCode = false

// checkReply accepts exactly one reply variant: result, hello, need or error.
func checkReply(r map[string]any, id int64, op string) (string, error) {
	rest := map[string]any{}
	for k, v := range r {
		if k != "id" {
			rest[k] = v
		}
	}
	if _, isErr := rest["error"]; isErr {
		if _, ok := rest["error"].(string); !ok {
			return "", fmt.Errorf("error is not a string")
		}
		code := ""
		switch {
		case keysAre(rest, "error", "code"):
			c, ok := rest["code"].(string)
			if !ok || !errorCodes[c] {
				return "", fmt.Errorf("unknown error code: %v", rest["code"])
			}
			code = c
		case legacyErrorWithoutCode && keysAre(rest, "error"):
		default:
			return "", fmt.Errorf("error reply: fields %v (expected id, error, code)", rest)
		}
		// id null only for parse and size-limit failures (the request was not read);
		// otherwise the id must equal the request's.
		if idv, has := r["id"]; has && idv == nil && (code == "parse_error" || code == "too_large") {
			return "error", nil
		}
		if !natOK(r["id"]) || r["id"].(json.Number) != json.Number(strconv.FormatInt(id, 10)) {
			return "", fmt.Errorf("reply id %v instead of %d", r["id"], id)
		}
		return "error", nil
	}
	if !natOK(r["id"]) || r["id"].(json.Number) != json.Number(strconv.FormatInt(id, 10)) {
		return "", fmt.Errorf("reply id %v instead of %d", r["id"], id)
	}
	// hello: {protocol, states} and, in the engine's mutant mode (tests only), mutant.
	if op == "hello" && (keysAre(rest, "protocol", "states") || keysAre(rest, "protocol", "states", "mutant")) {
		if !natOK(rest["protocol"]) || !natOK(rest["states"]) {
			return "", fmt.Errorf("hello: %v", rest)
		}
		if rest["protocol"].(json.Number) != protocolVersion {
			return "", fmt.Errorf("hello: protocol %v, expected %s", rest["protocol"], protocolVersion)
		}
		if mu, ok := rest["mutant"]; ok && !nameOK(mu) {
			return "", fmt.Errorf("hello: mutant %v", mu)
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
	// "recovered" only in a reply to step (docs/format.md, section 8)
	if !keysAre(rest, "snapshot", "actions") && (op != "step" || !keysAre(rest, "snapshot", "actions", "recovered")) {
		return "", fmt.Errorf("reply to %s: fields %v", op, rest)
	}
	if rec, ok := rest["recovered"]; ok && rec != true {
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


func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<28)
	n, bad := 0, 0
	for sc.Scan() {
		var rec struct {
			Op   string `json:"op"`
			ID   int64  `json:"id"`
			Line string `json:"line"`
			Want string `json:"want"`
		}
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil {
			fmt.Println("bad input", err)
			os.Exit(2)
		}
		var resp map[string]any
		dec := json.NewDecoder(strings.NewReader(rec.Line))
		dec.UseNumber()
		got := ""
		if err := dec.Decode(&resp); err != nil {
			got = "ERR"
		} else if v, err := checkReply(resp, rec.ID, rec.Op); err != nil {
			got = "ERR:" + err.Error()
		} else {
			got = v
		}
		n++
		want := rec.Want
		if want == "" {
			want = "OK"
		}
		ok := (want == "OK" && !strings.HasPrefix(got, "ERR")) || (want == "ERR" && strings.HasPrefix(got, "ERR"))
		if !ok {
			bad++
			if bad < 20 {
				fmt.Printf("MISMATCH want %s got %s: %s %s\n", want, got, rec.Op, strings.TrimSpace(rec.Line))
			}
		}
	}
	fmt.Printf("go checkReply: %d replies, %d mismatches\n", n, bad)
	if bad > 0 {
		os.Exit(1)
	}
}
