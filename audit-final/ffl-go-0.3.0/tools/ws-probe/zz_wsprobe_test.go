package figram

import "testing"

// Probe: which bytes after a reply does decodeJSON accept? JSON (RFC 8259) allows only
// space, tab, LF and CR as whitespace; Python's json.loads rejects anything else as extra data.
func TestZZWhitespaceProbe(t *testing.T) {
	for _, c := range []struct {
		name string
		tail string
	}{{"space", " "}, {"tab", "\t"}, {"lf", "\n"}, {"crlf", "\r\n"}, {"vt", "\v"}, {"ff", "\f"}, {"nel", "\u0085"}, {"nbsp", " "}, {"x", "x"}} {
		_, err := decodeJSON([]byte(`{"id":1}` + c.tail))
		t.Logf("%-6s accepted=%v", c.name, err == nil)
	}
}
