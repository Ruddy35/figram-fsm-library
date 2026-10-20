// Проверка встроенного движка в процессе Go: эталонные сценарии из многих горутин.
//
//	go build && ./gofig [-rounds N] [-bots K] bot.json traces.jsonl
//	go build -race   — с детектором гонок (CI на многоядерной машине)
package main

/*
#cgo LDFLAGS: -L${SRCDIR}/../out -l:libfigram.a -L${SRCDIR}/../out/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lc++ -lc++abi -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt
#include <stdlib.h>
int figram_init(void);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
void figram_free(char*);
void figram_unload(void*);
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

// Bot — загруженный документ. Безопасен для одновременных вызовов Request;
// Close освобождает автомат и ждёт завершения текущих вызовов.
type Bot struct {
	mu     sync.RWMutex
	m      unsafe.Pointer
	nextID atomic.Int64
}

func Load(doc string) (*Bot, error) {
	// C-строка оканчивается на NUL: всё после первого NUL библиотека не увидела бы,
	// и документ с мусором в хвосте молча загрузился бы. CLI такой текст отвергает.
	if strings.IndexByte(doc, 0) >= 0 {
		return nil, fmt.Errorf("документ содержит байт NUL")
	}
	cs := C.CString(doc)
	defer C.free(unsafe.Pointer(cs))
	m := C.figram_load_doc(cs)
	if m == nil {
		return nil, fmt.Errorf("документ не прошёл проверку")
	}
	return &Bot{m: m}, nil
}

// Close освобождает автомат; после него Request возвращает ошибку.
func (b *Bot) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.m != nil {
		C.figram_unload(b.m)
		b.m = nil
	}
}

// Request — один запрос протокола движка. Ответ строго проверяется
// (checkReply): id, ровно один вариант ответа, типы полей.
func (b *Bot) Request(req map[string]any) (map[string]any, string, error) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.m == nil {
		return nil, "", fmt.Errorf("автомат освобождён")
	}
	id := b.nextID.Add(1)
	// Словарь вызывающего не изменяется: id добавляется в копию верхнего
	// уровня, вложенные значения сериализация только читает. Поэтому один
	// словарь можно передавать из многих горутин одновременно.
	msg := make(map[string]any, len(req)+1)
	for k, v := range req {
		msg[k] = v
	}
	msg["id"] = id
	line, err := json.Marshal(msg)
	if err != nil {
		return nil, "", fmt.Errorf("запрос не сериализуется: %w", err)
	}
	cs := C.CString(string(line))
	defer C.free(unsafe.Pointer(cs))
	out := C.figram_request_json(b.m, cs)
	defer C.figram_free(out)
	var resp map[string]any
	dec := json.NewDecoder(strings.NewReader(C.GoString(out)))
	dec.UseNumber()
	if err := dec.Decode(&resp); err != nil {
		return nil, "", fmt.Errorf("ответ не JSON-объект: %w", err)
	}
	if corrupt != nil {
		corrupt(resp)
	}
	// op может отсутствовать или быть не строкой: движок ответит error, паники нет.
	op, _ := req["op"].(string)
	variant, err := checkReply(resp, id, op)
	return resp, variant, err
}

// corrupt — только для самопроверки (флаг -corrupt): портит ответ движка,
// чтобы убедиться, что проверка ловит нарушение протокола.
var corrupt func(map[string]any)

var corruptions = map[string]func(map[string]any){
	"recovered-broken": func(r map[string]any) { r["recovered"] = "BROKEN" },
	"recovered-false":  func(r map[string]any) { r["recovered"] = false },
	"no-id":            func(r map[string]any) { delete(r, "id") },
	"extra-error":      func(r map[string]any) { r["error"] = "x" },
	"empty":            func(r map[string]any) { for k := range r { delete(r, k) } },
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

// checkSnapshot — строго по snapshot.schema.json.
func checkSnapshot(v any) error {
	s, ok := v.(map[string]any)
	if !ok {
		return fmt.Errorf("снимок не объект: %v", v)
	}
	for k := range s {
		switch k {
		case "v", "at", "resume", "timers", "seq":
		default:
			return fmt.Errorf("снимок: лишнее поле %q", k)
		}
	}
	if v, ok := s["v"]; ok && (!natOK(v) || v.(json.Number) != "1") {
		return fmt.Errorf("снимок: версия %v", v)
	}
	if !nameOK(s["at"]) {
		return fmt.Errorf("снимок.at: %v", s["at"])
	}
	if r, ok := s["resume"]; ok {
		m, ok := r.(map[string]any)
		if !ok {
			return fmt.Errorf("снимок.resume не объект")
		}
		for k, v := range m {
			if k == "" || !nameOK(v) {
				return fmt.Errorf("снимок.resume.%q: %v", k, v)
			}
		}
	}
	if t, ok := s["timers"]; ok {
		m, ok := t.(map[string]any)
		if !ok {
			return fmt.Errorf("снимок.timers не объект")
		}
		for k, v := range m {
			if k == "" || !natOK(v) {
				return fmt.Errorf("снимок.timers.%q: %v", k, v)
			}
		}
	}
	if q, ok := s["seq"]; ok && !natOK(q) {
		return fmt.Errorf("снимок.seq: %v", q)
	}
	return nil
}

func checkActions(v any) error {
	acts, ok := v.([]any)
	if !ok {
		return fmt.Errorf("actions не список: %v", v)
	}
	for _, a := range acts {
		m, ok := a.(map[string]any)
		if !ok {
			return fmt.Errorf("действие не объект: %v", a)
		}
		switch {
		case keysAre(m, "send"), keysAre(m, "call"), keysAre(m, "cancel_timer"):
			for _, x := range m {
				if !nameOK(x) {
					return fmt.Errorf("действие: %v", m)
				}
			}
		case keysAre(m, "start_timer", "ms", "token"):
			if !nameOK(m["start_timer"]) || !natOK(m["ms"]) || !natOK(m["token"]) {
				return fmt.Errorf("start_timer: %v", m)
			}
		default:
			return fmt.Errorf("неизвестная форма действия: %v", m)
		}
	}
	return nil
}

// checkReply — ровно один вариант ответа: результат, need или error.
func checkReply(r map[string]any, id int64, op string) (string, error) {
	if !natOK(r["id"]) || r["id"].(json.Number) != json.Number(strconv.FormatInt(id, 10)) {
		return "", fmt.Errorf("id ответа %v вместо %d", r["id"], id)
	}
	rest := map[string]any{}
	for k, v := range r {
		if k != "id" {
			rest[k] = v
		}
	}
	if keysAre(rest, "error") {
		if _, ok := rest["error"].(string); !ok {
			return "", fmt.Errorf("error не строка")
		}
		return "error", nil
	}
	if op == "hello" && keysAre(rest, "protocol", "states") {
		if !natOK(rest["protocol"]) || !natOK(rest["states"]) {
			return "", fmt.Errorf("hello: %v", rest)
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
		return "", fmt.Errorf("ответ %s: поля %v", op, rest)
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

// validateTrace — ожидания сценария тоже обязаны соответствовать протоколу.
func validateTrace(t trace) error {
	if t.Start != nil {
		s, err := decodeNum(t.Start.Snapshot)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("начальный снимок сценария: %v %v", err, checkSnapshot(s))
		}
		a, err := decodeNum(t.Start.Actions)
		if err != nil || checkActions(a) != nil {
			return fmt.Errorf("начальные действия сценария")
		}
	} else {
		s, err := decodeNum(t.From)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("исходный снимок сценария (from)")
		}
	}
	for i, st := range t.Steps {
		s, err := decodeNum(st.Snapshot)
		if err != nil || checkSnapshot(s) != nil {
			return fmt.Errorf("шаг %d: снимок сценария", i)
		}
		a, err := decodeNum(st.Actions)
		if err != nil || checkActions(a) != nil {
			return fmt.Errorf("шаг %d: действия сценария", i)
		}
		if st.Recovered != nil && !*st.Recovered {
			return fmt.Errorf("шаг %d: recovered может быть только true", i)
		}
		if st.Event == "" {
			return fmt.Errorf("шаг %d: пустое событие", i)
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
				fmt.Fprintln(os.Stderr, "нарушение протокола:", err)
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
				fmt.Fprintln(os.Stderr, "нарушение протокола:", err)
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
	rounds := flag.Int("rounds", 1, "сколько раз прогнать все сценарии")
	bots := flag.Int("bots", 1, "сколько раз загрузить документ (общие автоматы для горутин)")
	corruptKind := flag.String("corrupt", "", "самопроверка: испортить ответы (recovered-broken, no-id, …)")
	reload := flag.Int("reload", 0, "проверка времени жизни: загрузить и освободить документ N раз")
	flag.Parse()
	if *corruptKind != "" {
		f, ok := corruptions[*corruptKind]
		if !ok {
			fail("неизвестная порча %q", *corruptKind)
		}
		corrupt = f
	}
	if *rounds < 1 || *bots < 1 {
		fail("-rounds и -bots должны быть не меньше 1 (получено %d и %d)", *rounds, *bots)
	}
	if flag.NArg() != 2 {
		fail("использование: gofig [-rounds N] [-bots K] bot.json traces.jsonl")
	}
	if C.figram_init() != 0 {
		panic("init")
	}
	doc, err := os.ReadFile(flag.Arg(0))
	if err != nil {
		fail("не прочитать документ: %v", err)
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
		fail("не открыть сценарии: %v", err)
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
			fail("строка %d сценариев повреждена: %v", line, err)
		}
		if t.Start == nil && len(t.From) == 0 || len(t.Steps) == 0 {
			fail("строка %d: сценарий без начала или без шагов", line)
		}
		if err := validateTrace(t); err != nil {
			fail("строка %d: ожидания сценария нарушают протокол: %v", line, err)
		}
		traces = append(traces, t)
	}
	if err := sc.Err(); err != nil {
		fail("ошибка чтения сценариев: %v", err)
	}
	if len(traces) == 0 {
		fail("сценариев нет")
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
		fail("ожидаемое число запросов %d — проверять нечего", expected)
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
	fmt.Printf("ботов %d, раундов %d, горутин %d, запросов %d, расхождений %d, %.0f запросов/с (GOMAXPROCS=%d, CPU=%d)\n",
		*bots, *rounds, len(traces)**rounds, total.Load(), bad.Load(), float64(total.Load())/el.Seconds(), runtime.GOMAXPROCS(0), runtime.NumCPU())
	for _, b := range loaded {
		b.Close()
	}
	if _, _, err := loaded[0].Request(map[string]any{"op": "hello"}); err == nil {
		fail("запрос после Close не отвергнут")
	}
	if total.Load() != expected {
		fail("обработано запросов %d из %d", total.Load(), expected)
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

// reloadTest: загрузка, запрос и освобождение n раз; рост памяти против контроля без Close.
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
				fail("запрос к загруженному автомату: %v", err)
			}
			if close {
				b.Close()
			}
		}
		runtime.GC()
		debug.FreeOSMemory()
		return rssKB() - before
	}
	run(true) // прогрев: аллокаторы Go и Lean выходят на установившийся размер
	withClose := run(true)
	withoutClose := run(false)
	fmt.Printf("%d загрузок с Close (после прогрева): рост памяти %d КБ; без Close (контроль): %d КБ\n",
		n, withClose, withoutClose)
	// Рост с Close двухмодальный: ~0 или ~2–2.5 МБ (шаг аллокатора), порог 2048 КБ давал
	// ложные отказы в 20–40 % прогонов под нагрузкой. 4096 КБ — всё ещё в 12 раз ниже контроля.
	if withClose >= 4096 || withoutClose <= 8192 {
		fail("Close не освобождает память или контроль нечувствителен")
	}
}
