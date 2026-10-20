// fgsigprobe: does the sigaltstack that the Lean runtime installs during figram_init
// (malloc'ed SIGSTKSZ bytes on the calling Go M) survive Go signal traffic?
// The goroutine that ran Init stays locked to that M and keeps running Go code with
// deep stacks while SIGPROF (CPU profile, 1 kHz) and SIGURG (async preemption) hit it.
//
//	FIGRAM_KEEP_LEAN_SIGALTSTACK=1 fgsigprobe -secs 60   # Lean's stack left in place
//	fgsigprobe -secs 60                                  # binding restores Go's stack
package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"runtime"
	"runtime/pprof"
	"sync/atomic"
	"syscall"
	"time"

	"figramhost/figram"
)

//go:noinline
func deep(n int) int {
	var pad [64]byte
	pad[n%64] = byte(n)
	if n == 0 {
		return int(pad[0])
	}
	return deep(n-1) + int(pad[n%64])
}

func main() {
	secs := flag.Int("secs", 30, "duration")
	flag.Parse()
	runtime.SetCPUProfileRate(1000)
	if err := pprof.StartCPUProfile(io.Discard); err != nil {
		panic(err)
	}
	var tid atomic.Int64
	var iters atomic.Int64
	ready := make(chan struct{})
	go func() {
		runtime.LockOSThread() // keep the M on which Lean set its sigaltstack
		if err := figram.Init(); err != nil {
			panic(err)
		}
		tid.Store(int64(syscall.Gettid()))
		close(ready)
		for {
			deep(200)
			iters.Add(1)
		}
	}()
	<-ready
	fmt.Printf("init=%+v tid=%d keep=%q\n", figram.InitReport(), tid.Load(), os.Getenv("FIGRAM_KEEP_LEAN_SIGALTSTACK"))
	// other goroutines keep all Ps busy so the scheduler preempts (SIGURG) a lot
	for i := 0; i < 3; i++ {
		go func() {
			for {
				deep(100)
			}
		}()
	}
	end := time.Now().Add(time.Duration(*secs) * time.Second)
	for time.Now().Before(end) {
		time.Sleep(time.Second)
		// direct signal storm at the Lean-stack thread as well
		for k := 0; k < 200; k++ {
			syscall.Tgkill(os.Getpid(), int(tid.Load()), syscall.SIGURG)
		}
	}
	pprof.StopCPUProfile()
	fmt.Printf("survived: iterations=%d\n", iters.Load())
}
