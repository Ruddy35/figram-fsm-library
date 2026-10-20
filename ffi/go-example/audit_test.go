package main

// Аудит обёртки Bot (ffi-go): Close против потока Request, неверный op,
// NUL в строках, большие запросы, масштабирование и длительная нагрузка.
// Длительные тесты включаются переменными окружения:
//   FIGRAM_SCALING=секунд_на_замер  FIGRAM_SOAK=секунд  FIGRAM_SIGNALS=секунд

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"runtime/pprof"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func auditDoc(t testing.TB) string {
	doc, err := os.ReadFile("../../examples/pizza.bot.json")
	if err != nil {
		t.Fatal(err)
	}
	if initFigram() != 0 {
		t.Fatal("init")
	}
	return string(doc)
}

var stepReq = map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"}, "event": "btn:order"}

// Close из нескольких горутин посреди потока Request: ни паники, ни гонки,
// до Close — корректные ответы, после — только ошибка «автомат освобождён».
func TestAuditCloseRequestStorm(t *testing.T) {
	doc := auditDoc(t)
	var ok, closed, other atomic.Int64
	for round := 0; round < 100; round++ {
		bot, err := Load(doc)
		if err != nil {
			t.Fatal(err)
		}
		var wg sync.WaitGroup
		stop := make(chan struct{})
		for g := 0; g < 16; g++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					select {
					case <-stop:
						return
					default:
					}
					_, v, err := bot.Request(stepReq)
					switch {
					case err == nil && v == "result":
						ok.Add(1)
					case err != nil && err.Error() == "автомат освобождён":
						closed.Add(1)
					default:
						other.Add(1)
						t.Errorf("unexpected: %v %v", v, err)
					}
				}
			}()
		}
		time.Sleep(2 * time.Millisecond)
		var cw sync.WaitGroup
		for c := 0; c < 4; c++ {
			cw.Add(1)
			go func() { defer cw.Done(); bot.Close() }()
		}
		cw.Wait()
		time.Sleep(time.Millisecond)
		close(stop)
		wg.Wait()
		if _, _, err := bot.Request(stepReq); err == nil {
			t.Fatal("request after Close accepted")
		}
	}
	t.Logf("100 rounds × 16 goroutines: ok=%d after-close errors=%d other=%d", ok.Load(), closed.Load(), other.Load())
	if ok.Load() == 0 || closed.Load() == 0 {
		t.Fatalf("storm did not exercise both sides: ok=%d closed=%d", ok.Load(), closed.Load())
	}
}

func requestNoPanic(b *Bot, req map[string]any) (resp map[string]any, v string, err error, p any) {
	defer func() { p = recover() }()
	resp, v, err = b.Request(req)
	return
}

// Request({"op":"hello"}) на ОТКРЫТОМ автомате: проверка в main.go «запрос после
// Close не отвергнут» использует именно hello и потому не может покраснеть.
func TestAuditHelloAlwaysError(t *testing.T) {
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	resp, v, err := bot.Request(map[string]any{"op": "hello"})
	t.Logf("open bot, hello → resp=%v variant=%q err=%v", resp, v, err)
	if err != nil {
		t.Errorf("hello on an open bot returns an error, so main.go's post-Close check is vacuous: %v", err)
	}
}

// Request без op или с op не-строкой: движок отвечает error, обёртка не должна паниковать.
func TestAuditRequestBadOp(t *testing.T) {
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	for _, req := range []map[string]any{{}, {"op": 1}, {"op": nil}, {"op": []any{"step"}}} {
		resp, v, err, p := requestNoPanic(bot, req)
		if p != nil {
			t.Errorf("Request(%v) panicked: %v", req, p)
			continue
		}
		if err != nil || v != "error" {
			t.Errorf("Request(%v) = %v %q %v, want error variant", req, resp, v, err)
		}
	}
	// После паники (если была) RWMutex не должен остаться захваченным.
	done := make(chan struct{})
	go func() { bot.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Close deadlocked after Request panic")
	}
}

// Документ с NUL посередине: C.CString передаёт строку, а strlen в
// lean_mk_string обрезает её — хвост молча игнорируется. CLI тот же текст отвергает.
func TestAuditLoadNUL(t *testing.T) {
	doc := auditDoc(t)
	glued := doc + "\x00{\"this is\": garbage"
	if b, err := Load(glued); err == nil {
		b.Close()
		t.Errorf("Load accepted a document with NUL and trailing garbage (%d bytes, %d after NUL)",
			len(glued), len(glued)-len(doc)-1)
	}
	// Тот же текст через CLI (если собран) — для сравнения.
	if cli := os.Getenv("FIGRAM_CLI"); cli != "" {
		f := t.TempDir() + "/nul.json"
		os.WriteFile(f, []byte(glued), 0o644)
		out, err := exec.Command(cli, "check", f).CombinedOutput()
		t.Logf("CLI %s check on the same bytes: err=%v out=%.120s", cli, err, out)
	}
}

// NUL внутри значений запроса: json.Marshal экранирует его (\u0000), до C он не доходит.
func TestAuditRequestNUL(t *testing.T) {
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	resp, v, err := bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu\x00x"}, "event": "btn:order"})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("NUL in snapshot.at → variant %s resp %v", v, resp)
	resp, v, err = bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"}, "event": "btn:order\x00"})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("NUL in event → variant %s resp %v", v, resp)
}

func rssNowKB() int64 {
	b, _ := os.ReadFile("/proc/self/status")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "VmRSS:") {
			n, _ := strconv.ParseInt(strings.Fields(l)[1], 10, 64)
			return n
		}
	}
	return 0
}

func hwmKB() int64 {
	b, _ := os.ReadFile("/proc/self/status")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "VmHWM:") {
			n, _ := strconv.ParseInt(strings.Fields(l)[1], 10, 64)
			return n
		}
	}
	return 0
}

// Большие запросы: длинное событие и объект guards из многих ключей.
// Фиксируются время и пик памяти (VmHWM), ответ обязан быть корректным.
func TestAuditLargeRequests(t *testing.T) {
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	for _, mb := range []int{1, 8, 32} {
		ev := "btn:" + strings.Repeat("x", mb<<20)
		h0 := hwmKB()
		t0 := time.Now()
		_, v, err := bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "menu"}, "event": ev})
		t.Logf("event %d MB: variant=%s err=%v time=%v VmHWM %d→%d MB", mb, v, err, time.Since(t0).Round(time.Millisecond), h0>>10, hwmKB()>>10)
		if err != nil || v != "result" {
			t.Errorf("event %d MB: %v %v", mb, v, err)
		}
	}
	for _, n := range []int{1000, 4000, 16000} {
		g := make(map[string]any, n)
		for i := 0; i < n; i++ {
			g[fmt.Sprintf("g%06d", i)] = true
		}
		t0 := time.Now()
		_, v, err := bot.Request(map[string]any{"op": "step", "snapshot": map[string]any{"at": "cart"}, "event": "btn:checkout", "guards": g})
		t.Logf("guards with %d keys: variant=%s err=%v time=%v", n, v, err, time.Since(t0).Round(time.Millisecond))
	}
}

// Пропускная способность 1/2/4 горутин: один общий автомат против отдельного на горутину.
func TestAuditScaling(t *testing.T) {
	secs, _ := strconv.ParseFloat(os.Getenv("FIGRAM_SCALING"), 64)
	if secs <= 0 {
		t.Skip("FIGRAM_SCALING not set")
	}
	doc := auditDoc(t)
	measure := func(k int, separate bool) float64 {
		bots := make([]*Bot, k)
		shared, _ := Load(doc)
		for i := range bots {
			if separate {
				bots[i], _ = Load(doc)
			} else {
				bots[i] = shared
			}
		}
		var n atomic.Int64
		var wg sync.WaitGroup
		deadline := time.Now().Add(time.Duration(secs * float64(time.Second)))
		t0 := time.Now()
		for i := 0; i < k; i++ {
			wg.Add(1)
			go func(b *Bot) {
				defer wg.Done()
				runtime.LockOSThread()
				for time.Now().Before(deadline) {
					if _, _, err := b.Request(stepReq); err != nil {
						t.Error(err)
						return
					}
					n.Add(1)
				}
			}(bots[i])
		}
		wg.Wait()
		rate := float64(n.Load()) / time.Since(t0).Seconds()
		shared.Close()
		for _, b := range bots {
			b.Close()
		}
		return rate
	}
	for rep := 0; rep < 3; rep++ {
		for _, k := range []int{1, 2, 4} {
			s := measure(k, false)
			p := measure(k, true)
			t.Logf("rep %d goroutines=%d GOMAXPROCS=%d shared=%.0f req/s separate=%.0f req/s ratio=%.2f",
				rep, k, runtime.GOMAXPROCS(0), s, p, s/p)
		}
	}
}

// Длительная нагрузка: общий автомат + циклы Load/Close других; RSS каждые 10 с.
func TestAuditSoak(t *testing.T) {
	secs, _ := strconv.Atoi(os.Getenv("FIGRAM_SOAK"))
	if secs <= 0 {
		t.Skip("FIGRAM_SOAK not set")
	}
	doc := auditDoc(t)
	bot, err := Load(doc)
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	stop := make(chan struct{})
	var reqs_total, cycles, bad atomic.Int64
	noClose := os.Getenv("FIGRAM_SOAK_NOCLOSE")
	var wg sync.WaitGroup
	for g := 0; g < 16; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			// «hello» не годится: checkReply считает его ответ нарушением протокола.
			reqs := []map[string]any{stepReq, {"op": "start"},
				{"op": "step", "snapshot": map[string]any{"at": "cart"}, "event": "btn:checkout", "guards": map[string]any{"cart_not_empty": true}}}
			for i := 0; ; i++ {
				select {
				case <-stop:
					return
				default:
				}
				if _, v, err := bot.Request(reqs[(g+i)%len(reqs)]); err != nil || v != "result" {
					bad.Add(1)
				}
				reqs_total.Add(1)
			}
		}(g)
	}
	for g := 0; g < 2; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				b, err := Load(doc)
				if err != nil {
					bad.Add(1)
					continue
				}
				if _, v, err := b.Request(map[string]any{"op": "start"}); err != nil || v != "result" {
					bad.Add(1)
				}
				if noClose == "" { // FIGRAM_SOAK_NOCLOSE=1 — контроль: утечка обязана быть замечена
					b.Close()
				}
				cycles.Add(1)
			}
		}()
	}
	var samples []int64
	start := time.Now()
	for time.Since(start) < time.Duration(secs)*time.Second {
		time.Sleep(10 * time.Second)
		runtime.GC()
		samples = append(samples, rssNowKB())
		t.Logf("t=%3.0fs RSS=%d KB requests=%d cycles=%d bad=%d", time.Since(start).Seconds(), samples[len(samples)-1], reqs_total.Load(), cycles.Load(), bad.Load())
	}
	close(stop)
	wg.Wait()
	if len(samples) < 4 {
		t.Fatal("too few samples")
	}
	warm := samples[1]
	last := samples[len(samples)-1]
	max := warm
	for _, s := range samples[1:] {
		if s > max {
			max = s
		}
	}
	t.Logf("soak %ds: load/close cycles=%d bad=%d RSS after warm-up %d KB, last %d KB, max %d KB, growth %d KB",
		secs, cycles.Load(), bad.Load(), warm, last, max, last-warm)
	if bad.Load() != 0 || cycles.Load() == 0 {
		t.Fatalf("bad=%d cycles=%d", bad.Load(), cycles.Load())
	}
	if last-warm > 32<<10 {
		t.Fatalf("RSS grew by %d KB after warm-up", last-warm)
	}
}

// Сигналы: профилировщик шлёт SIGPROF всем потокам, пока горутины работают
// внутри Lean (поток, вызвавший figram_init, получил sigaltstack от Lean).
func TestAuditSignals(t *testing.T) {
	secs, _ := strconv.Atoi(os.Getenv("FIGRAM_SIGNALS"))
	if secs <= 0 {
		t.Skip("FIGRAM_SIGNALS not set")
	}
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	f, _ := os.Create(t.TempDir() + "/cpu.prof")
	runtime.SetCPUProfileRate(1000)
	if err := pprof.StartCPUProfile(f); err != nil {
		t.Log("profile:", err)
	}
	var n, bad atomic.Int64
	var wg sync.WaitGroup
	deadline := time.Now().Add(time.Duration(secs) * time.Second)
	for g := 0; g < 32; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				if _, v, err := bot.Request(stepReq); err != nil || v != "result" {
					bad.Add(1)
				}
				n.Add(1)
				_ = make([]byte, 1024) // давление на GC
			}
		}()
	}
	wg.Wait()
	pprof.StopCPUProfile()
	t.Logf("signals: %d requests under SIGPROF at 1 kHz, bad=%d", n.Load(), bad.Load())
	if bad.Load() != 0 || n.Load() == 0 {
		t.Fatal("bad replies")
	}
}

// Одновременный figram_init из многих потоков в свежем процессе.
func TestAuditInitRace(t *testing.T) {
	if os.Getenv("FIGRAM_INIT_RACE_CHILD") == "1" {
		return
	}
	runs, _ := strconv.Atoi(os.Getenv("FIGRAM_INIT_RACE"))
	if runs <= 0 {
		t.Skip("FIGRAM_INIT_RACE not set")
	}
	fails := 0
	for i := 0; i < runs; i++ {
		cmd := exec.Command(os.Args[0], "-test.run=^$")
		cmd.Env = append(os.Environ(), "FIGRAM_INIT_RACE_CHILD=1")
		out, err := cmd.CombinedOutput()
		if err != nil {
			fails++
			if fails <= 3 {
				t.Logf("run %d: %v\n%.400s", i, err, out)
			}
		}
	}
	t.Logf("concurrent init: %d of %d processes failed", fails, runs)
	if fails > 0 {
		t.Fail()
	}
}

// Сколько потоков ОС создаёт Go при 1000 горутинах, одновременно вызывающих C:
// каждый поток один раз проходит lean_initialize_thread (enter_thread).
func TestAuditThreadCount(t *testing.T) {
	bot, err := Load(auditDoc(t))
	if err != nil {
		t.Fatal(err)
	}
	defer bot.Close()
	tasks := func() int { e, _ := os.ReadDir("/proc/self/task"); return len(e) }
	before := tasks()
	var wg sync.WaitGroup
	var bad atomic.Int64
	maxT := 0
	stop := make(chan struct{})
	sampled := make(chan struct{}) // the sampler's write of maxT happens before the read below
	go func() {
		defer close(sampled)
		for {
			select {
			case <-stop:
				return
			case <-time.After(5 * time.Millisecond):
				if n := tasks(); n > maxT {
					maxT = n
				}
			}
		}
	}()
	for g := 0; g < 1000; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 50; i++ {
				if _, v, err := bot.Request(stepReq); err != nil || v != "result" {
					bad.Add(1)
				}
			}
		}()
	}
	wg.Wait()
	close(stop)
	<-sampled
	t.Logf("1000 goroutines × 50 requests, GOMAXPROCS=%d: OS threads before %d, max during %d, after %d, bad %d",
		runtime.GOMAXPROCS(0), before, maxT, tasks(), bad.Load())
	if bad.Load() != 0 {
		t.Fatal("bad replies")
	}
}

// figram_init делает signal(SIGPIPE, SIG_IGN) для всего процесса-хоста. Сравниваем
// поведение Go при записи в закрытый stdout с init и без.
func TestAuditSIGPIPE(t *testing.T) {
	if os.Getenv("FIGRAM_SIGPIPE_CHILD") != "" {
		return
	}
	for _, mode := range []string{"noinit", "init"} {
		cmd := exec.Command(os.Args[0], "-test.run=^$")
		cmd.Env = append(os.Environ(), "FIGRAM_SIGPIPE_CHILD="+mode)
		out, _ := cmd.StdoutPipe()
		var stderr strings.Builder
		cmd.Stderr = &stderr
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		buf := make([]byte, 6)
		out.Read(buf) // дождаться «ready\n»
		out.Close()
		err := cmd.Wait()
		t.Logf("mode=%s: child exit: %v; stderr: %.200s", mode, err, stderr.String())
	}
}

func TestMain(m *testing.M) {
	if mode := os.Getenv("FIGRAM_SIGPIPE_CHILD"); mode != "" {
		if mode == "init" && initFigram() != 0 {
			os.Exit(3)
		}
		fmt.Println("ready")
		time.Sleep(100 * time.Millisecond)
		for i := 0; i < 1000; i++ {
			if _, err := fmt.Println("line"); err != nil {
				fmt.Fprintln(os.Stderr, "write error returned to Go:", err)
				os.Exit(4)
			}
		}
		os.Exit(0)
	}
	if os.Getenv("FIGRAM_INIT_RACE_CHILD") == "1" {
		doc, _ := os.ReadFile("../../examples/pizza.bot.json")
		var wg sync.WaitGroup
		start := make(chan struct{})
		var bad atomic.Int64
		for i := 0; i < 8; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				runtime.LockOSThread()
				<-start
				if initFigram() != 0 {
					bad.Add(1)
					return
				}
				b, err := Load(string(doc))
				if err != nil {
					bad.Add(1)
					return
				}
				if _, v, err := b.Request(map[string]any{"op": "start"}); err != nil || v != "result" {
					bad.Add(1)
				}
				b.Close()
			}()
		}
		time.Sleep(20 * time.Millisecond)
		close(start)
		wg.Wait()
		if bad.Load() != 0 {
			fmt.Println("init race: bad", bad.Load())
			os.Exit(1)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

var _ = json.Number("")
