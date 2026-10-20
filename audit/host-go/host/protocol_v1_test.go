package host

import (
	"strings"
	"testing"
	"time"
)

// Protocol v1 of the final engine (FORMAT.md §7): an error reply is exactly
// {id, error, code}; id null only when the request was not read (parse_error,
// too_large — e.g. C-level errors of the FFI path).
func TestParseReplyErrorCode(t *testing.T) {
	e := &Engines{Validate: true}
	cases := []struct {
		reply string
		ok    bool
		code  string
	}{
		{`{"id":5,"error":"x","code":"bad_snapshot"}`, true, "bad_snapshot"},
		{`{"id":5,"error":"seq_overflow","code":"seq_overflow"}`, true, "seq_overflow"},
		{`{"id":null,"error":"x","code":"parse_error"}`, true, "parse_error"},
		{`{"id":null,"error":"x","code":"too_large"}`, true, "too_large"},
		{`{"id":5,"error":"x"}`, false, ""},                            // code is mandatory
		{`{"id":5,"error":"x","code":"oops"}`, false, ""},              // unknown code
		{`{"id":5,"error":"x","code":7}`, false, ""},                   // code not a string
		{`{"id":5,"error":"x","code":"bad_request","x":1}`, false, ""}, // extra key
		{`{"id":null,"error":"x","code":"bad_request"}`, false, ""},    // null id only if not read
		{`{"id":6,"error":"x","code":"bad_request"}`, false, ""},       // foreign id
		{`{"error":"x","code":"parse_error"}`, false, ""},              // id missing
	}
	for _, c := range cases {
		r, err := e.parseReply([]byte(c.reply), 5, nil)
		if (err == nil) != c.ok {
			t.Errorf("%s: err=%v, want ok=%v", c.reply, err, c.ok)
			continue
		}
		if c.ok && (r.Kind != "error" || r.Code != c.code) {
			t.Errorf("%s: kind=%s code=%q", c.reply, r.Kind, r.Code)
		}
	}
}

// The embedded engine answers with codes, accepts start with seq (P-01) and
// rejects unknown request keys (strict requests).
func TestEngineCodesAndStartSeq(t *testing.T) {
	eng := NewEngines(bots, 5*time.Second, &Faults{})
	r, err := eng.Call("pizza", map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu", "bogus": 1},
		"event": "btn:x"}, -1)
	if err != nil || r.Kind != "error" || r.Code != "bad_snapshot" {
		t.Fatalf("invalid snapshot: %+v %v (want error code bad_snapshot)", r, err)
	}
	r, err = eng.Call("pizza", map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"},
		"event": "btn:x", "gaurds": map[string]any{}}, -1)
	if err != nil || r.Kind != "error" || r.Code != "bad_request" || !strings.Contains(r.Err, "gaurds") {
		t.Fatalf("unknown request key: %+v %v (want bad_request)", r, err)
	}
	r, err = eng.Call("pizza", map[string]any{"op": "start", "seq": 41}, 41)
	if err != nil || r.Kind != "result" || r.Snap.Seq != 42 {
		t.Fatalf("start with seq 41: %+v %v (want seq 42)", r, err)
	}
	r, err = eng.Call("pizza", map[string]any{"op": "start"}, -1)
	if err != nil || r.Kind != "result" || r.Snap.Seq != 1 {
		t.Fatalf("start without seq: %+v %v (want seq 1)", r, err)
	}
}

// Reset of an existing user continues its high-water seq (start with seq).
func TestResetContinuesHighWaterSeq(t *testing.T) {
	h := newTestHost(t, nil)
	for i := 0; i < 3; i++ { // cmd:start is a transition from every state
		if _, err := h.Step(upd("u1", 100+i, "cmd:start"), nil); err != nil {
			t.Fatal(err)
		}
	}
	hw, _ := hwOf(t, h, "u1")
	if hw != 3 {
		t.Fatalf("high-water seq %d after 3 transitions", hw)
	}
	if st, err := h.ResetUser("u1", "r1"); err != nil || st != "ok" {
		t.Fatalf("reset: %q %v", st, err)
	}
	hw2, _ := hwOf(t, h, "u1")
	if hw2 != hw+1 {
		t.Fatalf("reset: high-water seq %d → %d, want %d (seq must continue, never restart)", hw, hw2, hw+1)
	}
}
