package figram

// Audit harness (not part of ffl-go): verdicts of ffl-go's reply validator on a corpus, and the
// typed round trip of valid engine replies. Driven by environment variables; skipped otherwise.
//
//	AUDIT_JUDGE_IN=corpus.jsonl AUDIT_JUDGE_OUT=verdicts.tsv go test -run TestAuditJudge
//	AUDIT_RT_IN=valid.jsonl AUDIT_RT_OUT=rt.jsonl go test -run TestAuditRoundTrip

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"
)

type auditCase struct {
	K    string `json:"k"`
	Op   string `json:"op"`
	ID   int64  `json:"id"`
	Line string `json:"line"`
}

func auditCases(t *testing.T, path string) []auditCase {
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []auditCase
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 64<<20)
	for sc.Scan() {
		var c auditCase
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		out = append(out, c)
	}
	if len(out) == 0 {
		t.Fatal("empty corpus")
	}
	return out
}

// auditResult mirrors Bot.result after Bot.call (protocol.go): the conversion of a checked reply.
func auditResult(r map[string]any) *Result {
	delete(r, "id")
	if need, ok := r["need"]; ok {
		res := &Result{}
		for _, c := range need.([]any) {
			res.Need = append(res.Need, c.(string))
		}
		return res
	}
	res := &Result{Snapshot: toSnapshot(r["snapshot"].(map[string]any)), Recovered: r["recovered"] == true}
	for _, a := range r["actions"].([]any) {
		res.Actions = append(res.Actions, toAction(a.(map[string]any)))
	}
	return res
}

// auditVerdict is what Bot.call does with a reply line: decode, object, checkReply; for an
// accepted reply also the conversion of Bot.result / Bot.Hello / ProtocolError (a panic there is
// reported as PANIC).
func auditVerdict(c auditCase) (verdict string) {
	v, err := decodeJSON([]byte(c.Line))
	if err != nil {
		return "ERR"
	}
	r, ok := v.(map[string]any)
	if !ok {
		return "ERR"
	}
	variant, err := checkReply(r, c.ID, c.Op)
	if err != nil {
		return "ERR"
	}
	defer func() {
		if p := recover(); p != nil {
			verdict = "PANIC:" + fmt.Sprint(p)
		}
	}()
	switch variant {
	case "error":
		_ = &ProtocolError{Code: r["code"].(string), Message: r["error"].(string)}
	case "hello":
		_ = Hello{Protocol: toInt(r["protocol"]), States: toInt(r["states"])}
	default:
		auditResult(r)
	}
	return variant
}

func TestAuditJudge(t *testing.T) {
	in, outp := os.Getenv("AUDIT_JUDGE_IN"), os.Getenv("AUDIT_JUDGE_OUT")
	if in == "" {
		t.Skip("AUDIT_JUDGE_IN not set")
	}
	cases := auditCases(t, in)
	f, err := os.Create(outp)
	if err != nil {
		t.Fatal(err)
	}
	w := bufio.NewWriter(f)
	for _, c := range cases {
		fmt.Fprintf(w, "%s\t%s\n", c.K, auditVerdict(c))
	}
	if err := w.Flush(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	t.Logf("judged %d replies", len(cases))
}

// canon re-encodes JSON with sorted object keys and Go's escaping, to classify differences.
func canon(b []byte) string {
	var v any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		return "INVALID:" + err.Error()
	}
	out, _ := json.Marshal(v)
	return string(out)
}

// noHTMLEscape marshals v as encoding/json does, without the HTML escaping (<, >, &).
func noHTMLEscape(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "ERR:" + err.Error()
	}
	return strings.TrimSuffix(buf.String(), "\n")
}

// TestAuditRoundTrip converts every valid result/need reply to ffl-go's types and marshals them
// back; it compares the bytes with the engine's text: the snapshot, each action, and the whole
// reply without its id (Result.MarshalJSON). Differences are classified: "map-order" (equal
// after sorting object keys), "html-escape" (equal without Go's HTML escaping), "other".
func TestAuditRoundTrip(t *testing.T) {
	in, outp := os.Getenv("AUDIT_RT_IN"), os.Getenv("AUDIT_RT_OUT")
	if in == "" {
		t.Skip("AUDIT_RT_IN not set")
	}
	corrupt := os.Getenv("AUDIT_RT_CORRUPT") // negative control: corrupt the typed value
	cases := auditCases(t, in)
	f, err := os.Create(outp)
	if err != nil {
		t.Fatal(err)
	}
	w := bufio.NewWriter(f)
	counts := map[string]int{}
	for _, c := range cases {
		v, err := decodeJSON([]byte(c.Line))
		if err != nil {
			t.Fatalf("%s: %v", c.K, err)
		}
		r := v.(map[string]any)
		variant, err := checkReply(r, c.ID, c.Op)
		if err != nil {
			t.Fatalf("%s: valid engine reply rejected: %v\n%s", c.K, err, c.Line)
		}
		counts["variant:"+variant]++
		if variant != "result" && variant != "need" {
			continue
		}
		var raw struct {
			Snapshot json.RawMessage   `json:"snapshot"`
			Actions  []json.RawMessage `json:"actions"`
		}
		if err := json.Unmarshal([]byte(c.Line), &raw); err != nil {
			t.Fatal(err)
		}
		res := auditResult(r)
		switch corrupt {
		case "drop-detached":
			for i := range res.Actions {
				res.Actions[i].Detached = false
			}
		case "reverse-pending":
			p := res.Snapshot.Pending
			for i, j := 0, len(p)-1; i < j; i, j = i+1, j-1 {
				p[i], p[j] = p[j], p[i]
			}
		}
		emit := func(part, kind, got, want string) {
			counts[part+":"+kind]++
			if kind != "same" {
				b, _ := json.Marshal(map[string]string{"k": c.K, "part": part, "kind": kind, "got": got, "want": want})
				w.Write(append(b, '\n'))
			}
		}
		classify := func(got []byte, want []byte, v any) string {
			switch {
			case bytes.Equal(got, want):
				return "same"
			case noHTMLEscape(v) == string(want):
				return "html-escape"
			case canon(got) == canon(want):
				return "map-order"
			}
			return "other"
		}
		if variant == "need" {
			got, _ := json.Marshal(res)
			want := `{"need":` + strings.SplitN(c.Line, `"need":`, 2)[1]
			emit("reply", classify(got, []byte(want), res), string(got), want)
			continue
		}
		got, _ := json.Marshal(res.Snapshot)
		emit("snapshot", classify(got, raw.Snapshot, res.Snapshot), string(got), string(raw.Snapshot))
		if len(res.Actions) != len(raw.Actions) {
			emit("actions-count", "other", fmt.Sprint(len(res.Actions)), fmt.Sprint(len(raw.Actions)))
		} else {
			for i, a := range res.Actions {
				g, _ := json.Marshal(a)
				emit("action", classify(g, raw.Actions[i], a), string(g), string(raw.Actions[i]))
			}
		}
		// The whole reply without its id, as Result.MarshalJSON writes it.
		whole, _ := json.Marshal(res)
		want := "{" + strings.SplitN(c.Line, ",", 2)[1]
		emit("reply", classify(whole, []byte(want), res), string(whole), want)
	}
	w.Flush()
	f.Close()
	keys := make([]string, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		t.Logf("%-28s %d", k, counts[k])
	}
}
