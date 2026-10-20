// fgmem: memory/thread drift of the embedded engine across load/unload cycles and
// long request streams, measured inside one process (/proc/self/status).
//
//	fgmem -reloads 5000 -reqs 50 -goroutines 8
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
	"strings"
	"sync"
	"time"

	"figramhost/figram"
)

func status() map[string]string {
	f, _ := os.Open("/proc/self/status")
	defer f.Close()
	out := map[string]string{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		k, v, ok := strings.Cut(sc.Text(), ":")
		if ok && (k == "VmRSS" || k == "VmHWM" || k == "Threads" || k == "VmSize") {
			out[k] = strings.TrimSpace(v)
		}
	}
	return out
}

func main() {
	reloads := flag.Int("reloads", 5000, "load/unload cycles")
	reqs := flag.Int("reqs", 50, "requests per cycle per goroutine")
	gor := flag.Int("goroutines", 8, "concurrent requesters per cycle")
	docPath := flag.String("doc", "/home/user/figram-audit/hosts/shared/bots/pizza.bot.json", "document")
	churn := flag.Int("churn", 0, "thread churn: N goroutines that LockOSThread, call once and exit locked (the OS thread dies)")
	flag.Parse()
	d, err := os.ReadFile(*docPath)
	if err != nil {
		panic(err)
	}
	line := []byte(`{"id":7,"op":"step","snapshot":{"at":"cart","resume":{"order":"cart"},"timers":{"cart_reminder":5},"seq":5},"event":"btn:checkout","guards":{"cart_not_empty":true}}`)
	t0 := time.Now()
	sample := func(i int) {
		var ms runtime.MemStats
		runtime.ReadMemStats(&ms)
		b, _ := json.Marshal(map[string]any{"cycle": i, "t_s": time.Since(t0).Seconds(), "proc": status(),
			"go_heap": ms.HeapAlloc, "go_sys": ms.Sys, "goroutines": runtime.NumGoroutine(), "binding": figram.GetStats()})
		fmt.Println(string(b))
	}
	if *churn > 0 {
		m, err := figram.Load("pizza", d)
		if err != nil {
			panic(err)
		}
		for i := 0; i <= *churn; i++ {
			if i%(*churn/10) == 0 {
				runtime.GC()
				sample(i)
			}
			done := make(chan struct{})
			go func() {
				runtime.LockOSThread() // never unlocked: Go terminates this thread when the goroutine exits
				if _, err := m.Request(line); err != nil {
					panic(err)
				}
				close(done)
			}()
			<-done
		}
		m.Close()
		return
	}
	for i := 0; i <= *reloads; i++ {
		if i%(*reloads/10) == 0 {
			sample(i)
		}
		m, err := figram.Load("pizza", d)
		if err != nil {
			panic(err)
		}
		var wg sync.WaitGroup
		for g := 0; g < *gor; g++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for k := 0; k < *reqs; k++ {
					if _, err := m.Request(line); err != nil {
						panic(err)
					}
				}
			}()
		}
		wg.Wait()
		m.Close()
	}
	runtime.GC()
	sample(*reloads + 1)
}
