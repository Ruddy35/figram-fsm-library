package host

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"unicode/utf8"
)

const MaxInt = 9007199254740991 // 2^53 - 1

var intRe = regexp.MustCompile(`^(0|[1-9][0-9]{0,15})$`)

// ParseStrict decodes one JSON value: valid UTF-8, no duplicate keys at any level,
// no trailing data, numbers kept as json.Number.
func ParseStrict(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, errors.New("invalid UTF-8")
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := parseValue(dec, 0)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("trailing data after JSON value")
	}
	return v, nil
}

func parseValue(dec *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, errors.New("nesting too deep")
	}
	t, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch v := t.(type) {
	case json.Delim:
		switch v {
		case '{':
			obj := map[string]any{}
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, err
				}
				k, ok := kt.(string)
				if !ok {
					return nil, errors.New("object key is not a string")
				}
				if _, dup := obj[k]; dup {
					return nil, fmt.Errorf("duplicate key %q", k)
				}
				val, err := parseValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				obj[k] = val
			}
			if _, err := dec.Token(); err != nil {
				return nil, err
			}
			return obj, nil
		case '[':
			arr := []any{}
			for dec.More() {
				val, err := parseValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				arr = append(arr, val)
			}
			if _, err := dec.Token(); err != nil {
				return nil, err
			}
			return arr, nil
		}
		return nil, fmt.Errorf("unexpected delimiter %v", v)
	case string, bool, nil, json.Number:
		return v, nil
	}
	return nil, fmt.Errorf("unexpected token %T", t)
}

// AsUint53 accepts only a plain non-negative JSON integer 0..2^53-1 (no fraction,
// no exponent, no sign).
func AsUint53(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok || !intRe.MatchString(string(n)) {
		return 0, false
	}
	x, err := strconv.ParseInt(string(n), 10, 64)
	if err != nil || x > MaxInt {
		return 0, false
	}
	return x, true
}

// Snapshot is a validated user snapshot (FORMAT.md §7, snapshot.schema.json).
type Snapshot struct {
	At     string
	Resume map[string]string
	Timers map[string]int64
	Seq    int64
}

// ValidateSnapshot checks a snapshot strictly against snapshot.schema.json
// (integers must be plain integers; stricter than the schema for 1.0 / 1e0).
func ValidateSnapshot(v any) (*Snapshot, error) {
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, errors.New("snapshot is not an object")
	}
	s := &Snapshot{Resume: map[string]string{}, Timers: map[string]int64{}}
	for k, val := range obj {
		switch k {
		case "v":
			n, ok := AsUint53(val)
			if !ok || n != 1 {
				return nil, errors.New("snapshot: unknown version")
			}
		case "at":
			str, ok := val.(string)
			if !ok || str == "" {
				return nil, errors.New("snapshot: at must be a non-empty string")
			}
			s.At = str
		case "resume":
			m, ok := val.(map[string]any)
			if !ok {
				return nil, errors.New("snapshot: resume must be an object")
			}
			for rk, rv := range m {
				str, ok := rv.(string)
				if rk == "" || !ok || str == "" {
					return nil, errors.New("snapshot: bad resume entry")
				}
				s.Resume[rk] = str
			}
		case "timers":
			m, ok := val.(map[string]any)
			if !ok {
				return nil, errors.New("snapshot: timers must be an object")
			}
			for tk, tv := range m {
				n, ok := AsUint53(tv)
				if tk == "" || !ok {
					return nil, errors.New("snapshot: bad timer entry")
				}
				s.Timers[tk] = n
			}
		case "seq":
			n, ok := AsUint53(val)
			if !ok {
				return nil, errors.New("snapshot: seq must be an integer 0..2^53-1")
			}
			s.Seq = n
		default:
			return nil, fmt.Errorf("snapshot: unknown field %q", k)
		}
	}
	if s.At == "" {
		return nil, errors.New("snapshot: missing at")
	}
	return s, nil
}

// ParseSnapshotText parses + validates stored snapshot text.
func ParseSnapshotText(txt []byte) (*Snapshot, error) {
	v, err := ParseStrict(txt)
	if err != nil {
		return nil, fmt.Errorf("snapshot is not valid JSON: %v", err)
	}
	return ValidateSnapshot(v)
}
