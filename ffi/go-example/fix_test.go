package main

// Регрессионные тесты исправлений fix-ffi для обёртки Bot:
//   I-17  — Load без явного figram_init (ленивая инициализация, sync.Once);
//   P-09  — отказ figram_init под RLIMIT_AS — ошибка Load, а не SIGABRT процесса;
//   NP-06 — Load сообщает причину отказа документа;
//   PROTOCOL_CHANGES — checkReply: ошибки {id,error,code}, hello с mutant,
//   предел размера запроса (too_large, id null).
// Дочерние процессы запускаются через TestMain (FIGRAM_CHILD=…).

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

// runChild — тело дочернего процесса; код выхода 0 — ожидаемое поведение.
func runChild(mode string) int {
	doc, err := os.ReadFile("../../examples/pizza.bot.json")
	if err != nil {
		fmt.Println("read:", err)
		return 3
	}
	switch mode {
	case "lazy": // ни initFigram, ни figram_init: первый вызов — Load
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
	case "rlimit": // RLIMIT_AS задан до первого обращения к библиотеке
		as, _ := strconv.ParseUint(os.Getenv("FIGRAM_CHILD_AS"), 10, 64)
		if as == 0 {
			as = vmSizeBytes() + 1<<30 // заведомо меньше, чем нужно рантайму Lean (+2,2 ГБ)
		}
		var lim syscall.Rlimit
		syscall.Getrlimit(syscall.RLIMIT_AS, &lim)
		lim.Cur = as
		if err := syscall.Setrlimit(syscall.RLIMIT_AS, &lim); err != nil {
			fmt.Println("setrlimit:", err)
			return 3
		}
		b, err := Load(string(doc))
		if err != nil {
			fmt.Printf("RLIMIT_AS=%d: Load error: %v\n", as, err)
			if !strings.Contains(err.Error(), "figram_init") {
				return 1
			}
			// Повторная попытка возвращает ту же ошибку, процесс жив.
			if _, err2 := Load(string(doc)); err2 == nil {
				fmt.Println("second Load succeeded after a failed init")
				return 1
			}
			return 0
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

// I-17: Load в свежем процессе без явной инициализации.
func TestFixLazyLoadWithoutInit(t *testing.T) {
	out, err := child(t, "lazy")
	t.Logf("child: %v\n%.600s", err, out)
	if err != nil || !strings.Contains(out, "lazy: ok") {
		t.Fatalf("Load/Request without figram_init failed: %v", err)
	}
}

// P-09: под RLIMIT_AS figram_init не может создать поток рантайма; хост получает ошибку.
func TestFixInitUnderRlimitAS(t *testing.T) {
	if raceEnabled {
		t.Skip("под -race адресное пространство занято теневой памятью TSan")
	}
	for _, as := range []string{"", "3000000000"} { // "" — VmSize ребёнка + 1 ГиБ
		out, err := child(t, "rlimit", "FIGRAM_CHILD_AS="+as)
		t.Logf("RLIMIT_AS=%q: child %v\n%.800s", as, err, out)
		if err != nil {
			t.Errorf("RLIMIT_AS=%q: the process died instead of getting an error from Load: %v", as, err)
		}
	}
}

// NP-06: отказ загрузки документа объясняет причину.
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

// PROTOCOL_CHANGES 5: запрос длиннее предела (1 МиБ) — ответ too_large с id null, Request не падает.
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

// PROTOCOL_CHANGES 1, 7: проверка ответов знает про code и mutant.
func TestFixCheckReplyProtocolChanges(t *testing.T) {
	cases := []struct {
		op, reply, want string // want: вариант или "ERR"
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
