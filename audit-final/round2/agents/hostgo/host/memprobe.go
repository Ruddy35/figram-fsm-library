package main

// memprobe: is the RSS growth of the soak a leak or retention? A fixed workload for a while:
//   hostgo memprobe fixed DUR N G     — G goroutines send the same outcome request with N waits
//   hostgo memprobe reload DUR N G    — the same while the machine is reloaded every 5 ms
//   hostgo memprobe phases DUR N G    — N waits for DUR, then 0 waits for DUR, then N again
// Samples RSS, the number of OS threads, the Go heap and glibc malloc every 5 s.

import (
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"hostgo/rawfg"
)

func threadsNow() int {
	b, _ := os.ReadFile("/proc/self/status")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "Threads:") {
			n, _ := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(l, "Threads:")))
			return n
		}
	}
	return 0
}

func memprobeMain(args []string) {
	mode := args[0]
	d, _ := time.ParseDuration(args[1])
	n, _ := strconv.Atoi(args[2])
	g, _ := strconv.Atoi(args[3])
	doc := docText("points@1")
	var cur atomic.Pointer[handleBox]
	h, _ := rawfg.Load(doc)
	cur.Store(&handleBox{h: h})
	seq := int64(5_000_000)
	big := lineOf(map[string]any{"id": 1, "op": "step", "snapshot": pointsSnapshot(n, seq), "event": "call:award:ok", "token": seq})
	small := lineOf(map[string]any{"id": 1, "op": "step", "snapshot": pointsSnapshot(0, seq), "event": "btn:earn"})
	var line atomic.Pointer[[]byte]
	line.Store(&big)
	var reqs atomic.Int64
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < g; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				b := cur.Load()
				b.readers.Add(1)
				if b.gone.Load() {
					b.readers.Add(-1)
					continue
				}
				rawfg.Request(b.h, *line.Load())
				b.readers.Add(-1)
				reqs.Add(1)
			}
		}()
	}
	reloads := 0
	if mode == "reload" {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				case <-time.After(5 * time.Millisecond):
				}
				nh, _ := rawfg.Load(doc)
				old := cur.Swap(&handleBox{h: nh})
				old.gone.Store(true)
				for old.readers.Load() != 0 {
					time.Sleep(20 * time.Microsecond)
				}
				rawfg.Unload(old.h)
				reloads++
			}
		}()
	}
	t0 := time.Now()
	total := d
	if mode == "phases" {
		total = 3 * d
	}
	for time.Since(t0) < total {
		time.Sleep(5 * time.Second)
		el := time.Since(t0)
		if mode == "phases" {
			if el >= d && el < 2*d {
				line.Store(&small)
			} else if el >= 2*d {
				line.Store(&big)
			}
		}
		var ms runtime.MemStats
		runtime.ReadMemStats(&ms)
		fmt.Printf("t=%4.0fs rss=%7d KB threads=%3d goheap=%6d KB malloc=%6d KB requests=%9d reloads=%d waits=%d\n", el.Seconds(), rssKBNow(),
			threadsNow(), ms.HeapInuse/1024, rawfg.MallocInUse()/1024, reqs.Load(), reloads, len(*line.Load()))
	}
	close(stop)
	wg.Wait()
}
