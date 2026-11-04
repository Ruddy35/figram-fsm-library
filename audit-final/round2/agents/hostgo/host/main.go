package main

import (
	"flag"
	"runtime/pprof"
	"fmt"
	"os"
	"strings"
	"time"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: hostgo run|bench|abi|soak [flags]")
		os.Exit(2)
	}
	mode := os.Args[1]
	fs := flag.NewFlagSet(mode, flag.ExitOnError)
	users := fs.Int("users", 100, "users per bot")
	bots := fs.String("bots", "shop,points,booking", "documents")
	goals := fs.Int("goals", 2, "goals per user (orders, purchases, bookings)")
	maxActions := fs.Int("max-actions", 60, "actions per user")
	seed := fs.Int64("seed", 1, "seed")
	db := fs.String("db", "/tmp/claude-0/-home-user/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/host.db", "SQLite file")
	syncMode := fs.String("sync", "NORMAL", "SQLite synchronous mode (NORMAL, FULL)")
	crash := fs.Float64("crash", 0, "crash probability per crash point visit")
	points := fs.String("crash-points", "", "comma-separated crash points (default all)")
	dup := fs.Float64("dup", 0.1, "probability of a duplicated delivery (updates, timer and outcome events)")
	reload := fs.Duration("reload", 0, "reload a document every … (0: never)")
	unloadDelay := fs.Duration("unload-delay", 0, "pause between the swap and Close of the old machine")
	scale := fs.Float64("timescale", 0.01, "real ms per bot ms (timers)")
	notifyLat := fs.Duration("notify-latency", 100*time.Millisecond, "latency of the notification and audit services")
	replyWait := fs.Duration("reply-wait", 20*time.Second, "how long a user waits for a reply")
	drain := fs.Duration("drain", 120*time.Second, "drain timeout")
	bgw := fs.Int("bg-workers", 256, "background executor workers")
	execDelay := fs.Duration("exec-delay", 0, "delay of the sequential executor per action")
	out := fs.String("out", "", "JSON result file")
	control := fs.String("control", "", "negative control: noidem, norecon, nobarrier, inlinebg, nolock, outcome-separate, nodedup, pending-dropped, call-no-token")
	cpuprof := fs.String("cpuprofile", "", "CPU profile file")
	fs.Parse(os.Args[2:])
	if *cpuprof != "" {
		f, _ := os.Create(*cpuprof)
		pprof.StartCPUProfile(f)
		defer pprof.StopCPUProfile()
	}
	switch mode {
	case "run":
		cfg := &Config{crashProb: *crash, timeScale: *scale, unloadDelay: *unloadDelay, bgWorkers: *bgw, dupProb: *dup, seed: *seed, execDelay: *execDelay}
		if *points != "" {
			cfg.crashPoints = map[string]bool{}
			for _, p := range strings.Split(*points, ",") {
				cfg.crashPoints[p] = true
			}
		}
		for _, c := range strings.Split(*control, ",") {
			switch c {
			case "":
			case "noidem":
				cfg.noIdem = true
			case "norecon":
				cfg.noRecon = true
			case "nobarrier":
				cfg.noBarrier = true
			case "inlinebg":
				cfg.inlineBG = true
			case "nolock":
				cfg.noLock = true
			case "outcome-separate":
				cfg.outcomeSeparate = true
			case "nodedup":
				cfg.noDedup = true
			case "pending-dropped", "call-no-token", "detached-false":
				setCorruption(c)
			default:
				fmt.Fprintln(os.Stderr, "unknown control", c)
				os.Exit(2)
			}
		}
		o := ScenarioOpts{UsersPerBot: *users, Bots: strings.Split(*bots, ","), Goals: *goals, MaxActions: *maxActions, Seed: *seed,
			DB: *db, Sync: *syncMode, ReloadEvery: *reload, NotifyLatency: *notifyLat, ReplyWait: *replyWait, DrainTimeout: *drain, Out: *out}
		r := runScenario(cfg, o, *control)
		s, ok := summarize(r.Checks)
		fmt.Printf("control=%q users=%d crashes=%v instances=%d reloads=%d closed-retries=%d drained=%v elapsed=%.1fs\n",
			*control, len(o.Bots)**users, r.Crashes, r.Instances, r.Reloads, r.ClosedRetry, r.Drained, r.Elapsed)
		fmt.Printf("counts: %v\n", r.Counts)
		fmt.Printf("store: %v\n", txProf.String())
		fmt.Print(s)
		if !ok {
			fmt.Println("RESULT: FAIL")
			pprof.StopCPUProfile()
			os.Exit(1)
		}
		fmt.Println("RESULT: PASS")
	case "bench":
		benchMain(fs.Args())
	case "abi":
		abiMain(fs.Args())
	case "soak":
		soakMain(fs.Args())
	case "memprobe":
		memprobeMain(fs.Args())
	default:
		fmt.Fprintln(os.Stderr, "unknown mode", mode)
		os.Exit(2)
	}
}
