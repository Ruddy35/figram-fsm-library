// Reference Go wrapper of the embedded engine (docs/embedding.md) and gofig, a test driver that
// replays reference scenarios from many goroutines.
//
//	go build && ./gofig [-rounds N] [-bots K] bot.json traces.jsonl
//	go build -race   — with the race detector (CI on a multi-core machine)
package main

/*
// The linker flags of libfigram.a depend on the Lean toolchain: ffi/build.sh writes them to
// zz_figram_ldflags.go (next to this file, generated) and to ffi/out/LDFLAGS. To use the Bot
// wrapper in your own module: CGO_CFLAGS="-I path/to/ffi" CGO_LDFLAGS="$(cat path/to/ffi/out/LDFLAGS)"
// go build, or link the release library archive (docs/embedding.md).
#cgo CFLAGS: -I${SRCDIR}/../../ffi
#include <stdlib.h>
#include <string.h>
#include "figram.h"
// figram_last_error() is thread-local: it is copied in the same C call, because a goroutine
// may move to another OS thread between two cgo calls.
static int fg_init(char* buf, size_t cap) {
	int rc = figram_init();
	buf[0] = 0;
	if (rc != 0) {
		const char* e = figram_last_error();
		size_t n = e ? strlen(e) : 0;
		if (n >= cap) n = cap - 1;
		if (n) memcpy(buf, e, n);
		buf[n] = 0;
	}
	return rc;
}
*/
import "C"

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"reflect"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"
)

var (
	initOnce sync.Once
	initErr  error
)

// Init initializes the Lean runtime once per process (sync.Once; Load calls it itself).
// A failure is final and is returned by every call, for example when RLIMIT_AS leaves the
// runtime too little address space (see ffi/figram.h).
func Init() error {
	initOnce.Do(func() {
		buf := (*C.char)(C.malloc(1024))
		defer C.free(unsafe.Pointer(buf))
		if rc := C.fg_init(buf, 1024); rc != 0 {
			initErr = fmt.Errorf("figram_init = %d: %s", int(rc), C.GoString(buf))
		}
	})
	return initErr
}

// SetMaxRequest sets the request size limit in bytes for the whole process (0 — no limit;
// 1 MiB by default, like figram-engine --max-request) and returns the previous one. A request
// longer than the limit gets the reply {"id":null,"error":…,"code":"too_large"}.
func SetMaxRequest(n int) int {
	if n < 0 {
		n = 0
	}
	return int(C.figram_set_max_request(C.size_t(n)))
}

// Bot is a loaded document. It is safe for concurrent Request calls;
// Close frees the machine after waiting for the running calls.
type Bot struct {
	mu     sync.RWMutex
	m      unsafe.Pointer
	nextID atomic.Int64
}

// cBytes returns a pointer to the bytes of a Go string for a C call with an explicit length:
// the memory holds no Go pointers, and the library only reads it and does not keep it (the cgo
// rules allow this).
func cBytes(s string) (*C.char, C.size_t) {
	if len(s) == 0 {
		return nil, 0
	}
	return (*C.char)(unsafe.Pointer(unsafe.StringData(s))), C.size_t(len(s))
}

// Load loads a document (initializing the runtime on first use). The error contains the
// reason of the failure (figram_load_doc_ex_n): check errors, a parse error, UTF-8 or a failed init.
func Load(doc string) (*Bot, error) {
	if err := Init(); err != nil {
		return nil, err
	}
	// The library rejects an embedded NUL itself (_n variant); this gives a clear message.
	if strings.IndexByte(doc, 0) >= 0 {
		return nil, fmt.Errorf("document contains a NUL byte")
	}
	p, n := cBytes(doc)
	var cerr *C.char
	m := C.figram_load_doc_ex_n(p, n, &cerr)
	runtime.KeepAlive(doc)
	if m == nil {
		reason := "out of memory"
		if cerr != nil {
			reason = C.GoString(cerr)
			C.figram_free(cerr)
		}
		return nil, fmt.Errorf("document not loaded: %s", reason)
	}
	return &Bot{m: m}, nil
}

// Close frees the machine; after it Request returns an error.
func (b *Bot) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.m != nil {
		C.figram_unload(b.m)
		b.m = nil
	}
}

// Request sends one engine protocol request. The reply is validated strictly
// (checkReply): the id, exactly one reply variant, field types.
func (b *Bot) Request(req map[string]any) (map[string]any, string, error) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.m == nil {
		return nil, "", fmt.Errorf("bot is closed")
	}
	id := b.nextID.Add(1)
	// The caller's map is not modified: id is added to a copy of the top
	// level, and serialization only reads the nested values. So one map
	// may be passed from many goroutines at once.
	msg := make(map[string]any, len(req)+1)
	for k, v := range req {
		msg[k] = v
	}
	msg["id"] = id
	line, err := json.Marshal(msg)
	if err != nil {
		return nil, "", fmt.Errorf("request cannot be serialized: %w", err)
	}
	// The request is passed with its length (no copy, no cut at a NUL; json.Marshal escapes NUL).
	out := C.figram_request_json_n(b.m, (*C.char)(unsafe.Pointer(&line[0])), C.size_t(len(line)))
	runtime.KeepAlive(line)
	defer C.figram_free(out)
	var resp map[string]any
	dec := json.NewDecoder(strings.NewReader(C.GoString(out)))
	dec.UseNumber()
	if err := dec.Decode(&resp); err != nil {
		return nil, "", fmt.Errorf("reply is not a JSON object: %w", err)
	}
	if corrupt != nil {
		corrupt(resp)
	}
	// op may be missing or not a string: the engine replies with an error, no panic.
	op, _ := req["op"].(string)
	variant, err := checkReply(resp, id, op)
	return resp, variant, err
}

// corrupt is for the self-test only (flag -corrupt): it corrupts the engine reply
// to make sure that the check catches a protocol violation.
var corrupt func(map[string]any)

var corruptions = map[string]func(map[string]any){
	"recovered-broken": func(r map[string]any) { r["recovered"] = "BROKEN" },
	"recovered-false":  func(r map[string]any) { r["recovered"] = false },
	"no-id":            func(r map[string]any) { delete(r, "id") },
	"extra-error":      func(r map[string]any) { r["error"] = "x" },
	"empty": func(r map[string]any) {
		for k := range r {
			delete(r, k)
		}
	},
	"seq-bool": func(r map[string]any) {
		if s, ok := r["snapshot"].(map[string]any); ok {
			s["seq"] = true
		}
	},
	"null-snapshot": func(r map[string]any) { r["snapshot"] = nil },
}

const maxInt = 9007199254740991

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

// checkSnapshot validates strictly by schemas/snapshot.schema.json.
func checkSnapshot(v any) error {
	s, ok := v.(map[string]any)
	if !ok {
		return fmt.Errorf("snapshot is not an object: %v", v)
	}
	for k := range s {
		switch k {
		case "v", "at", "resume", "timers", "seq":
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
		if mu, ok := rest["mutant"]; ok && !nameOK(mu) {
			return "", fmt.Errorf("hello: mutant %v", mu)
		}
		return "hello", nil
	}
	if op == "step" && keysAre(rest, "need") {
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

type step struct {
	Event     string          `json:"event"`
	Token     json.RawMessage `json:"token"`
	Guards    map[string]bool `json:"guards"`
	Snapshot  json.RawMessage `json:"snapshot"`
	Actions   json.RawMessage `json:"actions"`
	Recovered *bool           `json:"recovered"`
}

type trace struct {
	Start *struct {
		Snapshot json.RawMessage `json:"snapshot"`
		Actions  json.RawMessage `json:"actions"`
	} `json:"start"`
	From  json.RawMessage `json:"from"`
	Steps []step          `json:"steps"`
}

func decodeNum(raw json.RawMessage) (any, error) {
	var v any
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	err := dec.Decode(&v)
	return v, err
}

// validateTrace: the expectations of a scenario must follow the protocol too.
func validateTrace(t trace) error {
	if t.Start != nil {
		s, err := decodeNum(t.Start.Snapshot)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("scenario start snapshot: %v %v", err, checkSnapshot(s))
		}
		a, err := decodeNum(t.Start.Actions)
		if err != nil || checkActions(a) != nil {
			return fmt.Errorf("scenario start actions")
		}
	} else {
		s, err := decodeNum(t.From)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("scenario initial snapshot (from)")
		}
	}
	for i, st := range t.Steps {
		s, err := decodeNum(st.Snapshot)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("step %d: scenario snapshot", i)
		}
		a, err := decodeNum(st.Actions)
		if err != nil || checkActions(a) != nil {
			return fmt.Errorf("step %d: scenario actions", i)
		}
		if st.Recovered != nil && !*st.Recovered {
			return fmt.Errorf("step %d: recovered can only be true", i)
		}
		if st.Event == "" {
			return fmt.Errorf("step %d: empty event", i)
		}
	}
	return nil
}

func same(a any, raw json.RawMessage) bool {
	b, err := decodeNum(raw)
	return err == nil && reflect.DeepEqual(a, b)
}

func check(b *Bot, t trace) (n int, bad int) {
	var snap any
	if t.Start != nil {
		r, variant, err := b.Request(map[string]any{"op": "start"})
		n++
		if err != nil || variant != "result" || !same(r["snapshot"], t.Start.Snapshot) || !same(r["actions"], t.Start.Actions) {
			bad++
			if err != nil {
				fmt.Fprintln(os.Stderr, "protocol violation:", err)
			}
		}
		snap, _ = decodeNum(t.Start.Snapshot)
	} else {
		snap, _ = decodeNum(t.From)
	}
	for _, st := range t.Steps {
		req := map[string]any{"op": "step", "snapshot": snap, "event": st.Event}
		if len(st.Token) > 0 {
			tok, _ := decodeNum(st.Token)
			req["token"] = tok
		}
		if len(st.Guards) > 0 {
			req["guards"] = st.Guards
		}
		r, variant, err := b.Request(req)
		n++
		wantRec := st.Recovered != nil && *st.Recovered
		_, gotRec := r["recovered"]
		if err != nil || variant != "result" || !same(r["snapshot"], st.Snapshot) ||
			!same(r["actions"], st.Actions) || gotRec != wantRec {
			bad++
			if err != nil {
				fmt.Fprintln(os.Stderr, "protocol violation:", err)
			}
		}
		snap, _ = decodeNum(st.Snapshot)
	}
	return
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func main() {
	rounds := flag.Int("rounds", 1, "how many times to run all scenarios")
	bots := flag.Int("bots", 1, "how many times to load the document (machines shared by the goroutines)")
	corruptKind := flag.String("corrupt", "", "self-test: corrupt the replies (recovered-broken, no-id, …)")
	reload := flag.Int("reload", 0, "lifetime check: load and close the document N times")
	flag.Parse()
	if *corruptKind != "" {
		f, ok := corruptions[*corruptKind]
		if !ok {
			fail("unknown corruption %q", *corruptKind)
		}
		corrupt = f
	}
	if *rounds < 1 || *bots < 1 {
		fail("-rounds and -bots must be at least 1 (got %d and %d)", *rounds, *bots)
	}
	if flag.NArg() != 2 {
		fail("usage: gofig [-rounds N] [-bots K] bot.json traces.jsonl")
	}
	if err := Init(); err != nil {
		fail("%v", err)
	}
	doc, err := os.ReadFile(flag.Arg(0))
	if err != nil {
		fail("cannot read the document: %v", err)
	}
	if *reload > 0 {
		reloadTest(string(doc), *reload)
		return
	}
	var loaded []*Bot
	for i := 0; i < *bots; i++ {
		bot, err := Load(string(doc))
		if err != nil {
			fail("%v", err)
		}
		loaded = append(loaded, bot)
	}
	f, err := os.Open(flag.Arg(1))
	if err != nil {
		fail("cannot open the scenarios: %v", err)
	}
	var traces []trace
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	line := 0
	for sc.Scan() {
		line++
		if len(sc.Bytes()) == 0 {
			continue
		}
		var t trace
		if err := json.Unmarshal(sc.Bytes(), &t); err != nil {
			fail("scenario line %d is corrupt: %v", line, err)
		}
		if t.Start == nil && len(t.From) == 0 || len(t.Steps) == 0 {
			fail("line %d: scenario without a start or without steps", line)
		}
		if err := validateTrace(t); err != nil {
			fail("line %d: scenario expectations violate the protocol: %v", line, err)
		}
		traces = append(traces, t)
	}
	if err := sc.Err(); err != nil {
		fail("error reading the scenarios: %v", err)
	}
	if len(traces) == 0 {
		fail("no scenarios")
	}
	expected := int64(0)
	for _, t := range traces {
		expected += int64(len(t.Steps))
		if t.Start != nil {
			expected++
		}
	}
	expected *= int64(*rounds)
	if expected <= 0 {
		fail("expected number of requests is %d: nothing to check", expected)
	}
	var total, bad atomic.Int64
	var wg sync.WaitGroup
	start := time.Now()
	for r := 0; r < *rounds; r++ {
		for i, t := range traces {
			wg.Add(1)
			go func(bot *Bot, t trace) {
				defer wg.Done()
				n, b := check(bot, t)
				total.Add(int64(n))
				bad.Add(int64(b))
			}(loaded[(i+r)%len(loaded)], t)
		}
	}
	wg.Wait()
	el := time.Since(start)
	fmt.Printf("bots %d, rounds %d, goroutines %d, requests %d, mismatches %d, %.0f requests/s (GOMAXPROCS=%d, CPU=%d)\n",
		*bots, *rounds, len(traces)**rounds, total.Load(), bad.Load(), float64(total.Load())/el.Seconds(), runtime.GOMAXPROCS(0), runtime.NumCPU())
	for _, b := range loaded {
		b.Close()
	}
	if _, _, err := loaded[0].Request(map[string]any{"op": "hello"}); err == nil {
		fail("request after Close was not rejected")
	}
	if total.Load() != expected {
		fail("processed %d of %d requests", total.Load(), expected)
	}
	if bad.Load() > 0 {
		os.Exit(1)
	}
}

func rssKB() int64 {
	b, err := os.ReadFile("/proc/self/statm")
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b))
	pages, _ := strconv.ParseInt(f[1], 10, 64)
	return pages * 4
}

// reloadTest loads, queries and closes the document n times; memory growth versus a control without Close.
func reloadTest(doc string, n int) {
	run := func(close bool) int64 {
		runtime.GC()
		debug.FreeOSMemory()
		before := rssKB()
		for i := 0; i < n; i++ {
			b, err := Load(doc)
			if err != nil {
				fail("%v", err)
			}
			if _, v, err := b.Request(map[string]any{"op": "start"}); err != nil || v != "result" {
				fail("request to the loaded machine: %v", err)
			}
			if close {
				b.Close()
			}
		}
		runtime.GC()
		debug.FreeOSMemory()
		return rssKB() - before
	}
	run(true) // warm-up: the Go and Lean allocators reach a steady size
	withClose := run(true)
	withoutClose := run(false)
	fmt.Printf("%d loads with Close (after warm-up): memory growth %d KB; without Close (control): %d KB\n",
		n, withClose, withoutClose)
	// The growth with Close is bimodal: ~0 or ~2–2.5 MB (an allocator step); a 2048 KB threshold
	// failed spuriously in 20–40 % of runs under load. 4096 KB is still 12 times below the control.
	if withClose >= 4096 || withoutClose <= 8192 {
		fail("Close does not free memory, or the control is insensitive")
	}
}
