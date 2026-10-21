package main

// Regression tests of the Bot wrapper:
//   - Load without an explicit figram_init (lazy initialization, sync.Once);
//   - the Lean runtime starts under RLIMIT_AS;
//   - Load reports why a document was rejected;
//   - checkReply: errors {id,error,code}, hello with mutant,
//     the request size limit (too_large, id null).
// Child processes are started through TestMain (FIGRAM_CHILD=…).

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
)

func pizzaDoc(t testing.TB) string {
	doc, err := os.ReadFile("../../examples/pizza.bot.json")
	if err != nil {
		t.Fatal(err)
	}
	return string(doc)
}

func vmSizeBytes() uint64 {
	b, _ := os.ReadFile("/proc/self/status")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "VmSize:") {
			n, _ := strconv.ParseUint(strings.Fields(l)[1], 10, 64)
			return n << 10
		}
	}
	return 0
}

// runChild is the body of a child process; exit code 0 means the expected behaviour.
func runChild(mode string) int {
	doc, err := os.ReadFile("../../examples/pizza.bot.json")
	if err != nil {
		fmt.Println("read:", err)
		return 3
	}
	switch mode {
	case "lazy": // neither initFigram nor figram_init: the first call is Load
		b, err := Load(string(doc))
		if err != nil {
			fmt.Println("Load without init:", err)
			return 1
		}
		_, v, err := b.Request(map[string]any{"op": "start"})
		b.Close()
		if err != nil || v != "result" {
			fmt.Println("Request without init:", v, err)
			return 1
		}
		fmt.Println("lazy: ok")
		return 0
	case "rlimit": // RLIMIT_AS is set before the first call into the library
		as, _ := strconv.ParseUint(os.Getenv("FIGRAM_CHILD_AS"), 10, 64)
		if h, _ := strconv.ParseUint(os.Getenv("FIGRAM_CHILD_AS_HEADROOM"), 10, 64); h != 0 {
			as = vmSizeBytes() + h // limit = current usage + headroom
		}
		var lim syscall.Rlimit
		syscall.Getrlimit(syscall.RLIMIT_AS, &lim)
		lim.Cur = as
		if err := syscall.Setrlimit(syscall.RLIMIT_AS, &lim); err != nil {
			fmt.Println("setrlimit:", err)
			return 3
		}
		// An explicit figram_init (as in main.go), then Load: on failure both must return an
		// error rather than crash the process (lean::exception → std::terminate → SIGABRT).
		rc := initFigram()
		fmt.Printf("RLIMIT_AS=%d: figram_init=%d\n", as, rc)
		b, err := Load(string(doc))
		if (rc != 0) != (err != nil) {
			fmt.Println("figram_init and Load disagree:", rc, err)
			return 1
		}
		if err != nil {
			fmt.Printf("RLIMIT_AS=%d: Load error: %v\n", as, err)
			if !strings.Contains(err.Error(), "figram_init") {
				return 1
			}
			// A second attempt returns the same error, the process stays alive.
			if _, err2 := Load(string(doc)); err2 == nil {
				fmt.Println("second Load succeeded after a failed init")
				return 1
			}
			return 0
		}
		if _, v, err := b.Request(map[string]any{"op": "start"}); err != nil || v != "result" {
			fmt.Println("Request under RLIMIT_AS:", v, err)
			return 1
		}
		b.Close()
		fmt.Printf("RLIMIT_AS=%d: loaded (enough address space)\n", as)
		return 0
	}
	fmt.Println("unknown child mode", mode)
	return 3
}

func child(t *testing.T, mode string, env ...string) (string, error) {
	cmd := exec.Command(os.Args[0], "-test.run=^$")
	cmd.Env = append(append(os.Environ(), "FIGRAM_CHILD="+mode), env...)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// Load in a fresh process without explicit initialization.
func TestFixLazyLoadWithoutInit(t *testing.T) {
	out, err := child(t, "lazy")
	t.Logf("child: %v\n%.600s", err, out)
	if err != nil || !strings.Contains(out, "lazy: ok") {
		t.Fatalf("Load/Request without figram_init failed: %v", err)
	}
}

// Init under RLIMIT_AS: the Lean runtime starts and works, because the mimalloc arena is not
// reserved in advance (ffi/figram_rt.c) and the runtime creates no threads (ffi/figram_init.cpp).
func TestFixInitUnderRlimitAS(t *testing.T) {
	if raceEnabled {
		t.Skip("under -race the address space is taken by the TSan shadow memory")
	}
	for _, env := range []string{"FIGRAM_CHILD_AS_HEADROOM=268435456", "FIGRAM_CHILD_AS_HEADROOM=1073741824",
		"FIGRAM_CHILD_AS=3000000000"} {
		out, err := child(t, "rlimit", env)
		t.Logf("%s: child %v\n%.800s", env, err, out)
		if err != nil || !strings.Contains(out, "loaded (enough address space)") {
			t.Errorf("%s: Load under RLIMIT_AS must succeed (err %v)", env, err)
		}
	}
}

// A failed document load explains the reason.
func TestFixLoadReason(t *testing.T) {
	_, err := Load(`{"figram":1}`)
	if err == nil {
		t.Fatal("bad document loaded")
	}
	t.Logf("Load(bad) → %v", err)
	if !strings.Contains(err.Error(), "bot") {
		t.Errorf("error does not explain the reason: %v", err)
	}
	_, err = Load(pizzaDoc(t) + "\x00garbage")
	if err == nil || !strings.Contains(err.Error(), "NUL") {
		t.Errorf("NUL in document: %v", err)
	}
}

// A request longer than the limit (1 MiB) gets a too_large reply with id null; Request does not crash.
func TestFixRequestTooLarge(t *testing.T) {
	bot, err := Load(pizzaDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	resp, v, err := bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"},
		"event": "btn:" + strings.Repeat("x", 2<<20)})
	t.Logf("2 MiB event → variant %q err %v code %v", v, err, resp["code"])
	if err != nil || v != "error" || resp["code"] != "too_large" || resp["id"] != nil {
		t.Errorf("want error variant with code too_large and id null, got %v %v %v", v, err, resp)
	}
	// Without the limit the engine processes the same request.
	if prev := SetMaxRequest(0); prev != 1<<20 {
		t.Errorf("default limit %d, want %d", prev, 1<<20)
	}
	defer SetMaxRequest(1 << 20)
	_, v, err = bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"},
		"event": "btn:" + strings.Repeat("x", 2<<20)})
	if err != nil || v != "result" {
		t.Errorf("limit 0: want result, got %v %v", v, err)
	}
}

func decodeReply(t *testing.T, s string) map[string]any {
	var r map[string]any
	dec := json.NewDecoder(strings.NewReader(s))
	dec.UseNumber()
	if err := dec.Decode(&r); err != nil {
		t.Fatal(err)
	}
	return r
}

// The reply check knows about code and mutant.
func TestFixCheckReplyProtocolChanges(t *testing.T) {
	cases := []struct {
		op, reply, want string // want: the variant or "ERR"
	}{
		{"step", `{"id":5,"error":"x","code":"bad_request"}`, "error"},
		{"step", `{"id":5,"error":"x","code":"bad_snapshot"}`, "error"},
		{"restart", `{"id":5,"error":"seq_overflow","code":"seq_overflow"}`, "error"},
		{"step", `{"id":5,"error":"x","code":"parse_error"}`, "error"},
		{"step", `{"id":null,"error":"x","code":"too_large"}`, "error"},
		{"step", `{"id":null,"error":"x","code":"parse_error"}`, "error"},
		{"step", `{"id":5,"error":"x","code":"nonsense"}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":""}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":7}`, "ERR"},
		{"step", `{"id":5,"error":"x","code":"bad_request","extra":1}`, "ERR"},
		{"step", `{"id":5,"error":1,"code":"bad_request"}`, "ERR"},
		{"step", `{"id":null,"error":"x","code":"bad_request"}`, "ERR"},
		{"step", `{"id":6,"error":"x","code":"bad_request"}`, "ERR"},
		{"step", `{"id":5,"error":"x"}`, "ERR"}, // code is required
		{"step", `{"id":null,"error":"x"}`, "ERR"},
		{"hello", `{"id":5,"protocol":1,"states":11}`, "hello"},
		{"hello", `{"id":5,"protocol":1,"states":11,"mutant":"drop-call:create_order"}`, "hello"},
		{"hello", `{"id":5,"protocol":1,"states":11,"mutant":""}`, "ERR"},
		{"hello", `{"id":5,"protocol":1,"states":11,"mutant":3}`, "ERR"},
		{"hello", `{"id":5,"protocol":1,"states":11,"extra":"x"}`, "ERR"},
		{"start", `{"id":5,"snapshot":{"at":"menu","seq":8},"actions":[{"send":"menu"}]}`, "result"},
	}
	for _, c := range cases {
		v, err := checkReply(decodeReply(t, c.reply), 5, c.op)
		got := v
		if err != nil {
			got = "ERR"
		}
		if got != c.want {
			t.Errorf("checkReply(%s, op=%s) = %q (%v), want %q", c.reply, c.op, v, err, c.want)
		}
	}
}
