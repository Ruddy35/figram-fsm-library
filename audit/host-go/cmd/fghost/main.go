// fghost: production-like Figram host with the engine embedded via cgo (libfigram.a).
//
//	fghost -db host.sqlite -sock host.sock -ext ext.sock -bots DIR -family pizza [-broken a,b] [-arm at:on,...]
package main

import (
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"figramhost/figram"
	"figramhost/host"
)

func main() {
	var (
		dbPath   = flag.String("db", "host.sqlite", "host database")
		sock     = flag.String("sock", "host.sock", "unix socket to listen on")
		extSock  = flag.String("ext", "ext.sock", "unix socket of the external simulator")
		bots     = flag.String("bots", "/home/user/figram-audit/hosts/shared/bots", "bot documents")
		family   = flag.String("family", "pizza", "bot family of new users")
		crashLog = flag.String("crashlog", "", "file where a firing crash point records itself")
		execs    = flag.Int("executors", 4, "outbox executor workers")
		busy     = flag.Int("busy-ms", 3000, "SQLite busy timeout")
		syncMode = flag.String("sync", "FULL", "PRAGMA synchronous")
		engT     = flag.Duration("engine-timeout", 3*time.Second, "engine call deadline")
		extT     = flag.Duration("ext-timeout", time.Second, "external client timeout")
		slots    = flag.Int("cgo-slots", 4, "max concurrent C calls")
		broken   = flag.String("broken", "", "comma-separated broken variants")
		arm      = flag.String("arm", "", "crash points armed at start: at[:on],...")
	)
	flag.Parse()
	figram.SetMaxConcurrentCalls(*slots)
	if err := figram.Init(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	b := map[string]bool{}
	for _, v := range strings.Split(*broken, ",") {
		if v == "" {
			continue
		}
		if _, ok := host.BrokenVariants[v]; !ok {
			fmt.Fprintf(os.Stderr, "unknown broken variant %q\n", v)
			os.Exit(2)
		}
		b[v] = true
	}
	var armed []host.CrashPoint
	for _, a := range strings.Split(*arm, ",") {
		if a == "" {
			continue
		}
		p := strings.SplitN(a, ":", 2)
		cp := host.CrashPoint{At: p[0]}
		if len(p) == 2 {
			cp.On = p[1]
		}
		armed = append(armed, cp)
	}
	h, err := host.New(host.Config{DB: *dbPath, Bots: *bots, Family: *family, ExtSock: *extSock, CrashLog: *crashLog,
		Executors: *execs, BusyMs: *busy, Sync: *syncMode, EngineTimeout: *engT, ExtTimeout: *extT, Broken: b, Armed: armed})
	if err != nil {
		fmt.Fprintln(os.Stderr, "host:", err)
		os.Exit(2)
	}
	// timers that became due before a crash fire now (callbacks after restart)
	if err := h.AdvanceTo(h.Clock()); err != nil {
		fmt.Fprintln(os.Stderr, "startup timers:", err)
	}
	if err := h.Serve(*sock); err != nil {
		fmt.Fprintln(os.Stderr, "serve:", err)
		os.Exit(1)
	}
}
