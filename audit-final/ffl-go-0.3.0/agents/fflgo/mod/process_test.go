package figram

// Tests that need a fresh process: the first call into the library starts the runtime, so they
// run in a child started through TestMain (FIGRAM_TEST_CHILD=<mode>).

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
)

func TestMain(m *testing.M) {
	if mode := os.Getenv("FIGRAM_TEST_CHILD"); mode != "" {
		os.Exit(child(mode))
	}
	os.Exit(m.Run())
}

// procStatus returns a field of /proc/self/status.
func procStatus(field string) string {
	b, _ := os.ReadFile("/proc/self/status")
	for _, l := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(l, field+":"); ok {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// child is the body of a child process: exit status 0 and "ok" on stdout mean success.
func child(mode string) int {
	doc, err := os.ReadFile("testdata/pizza.bot.json")
	if err != nil {
		fmt.Println(err)
		return 3
	}
	switch mode {
	case "lazy": // no Init: the first call is Load
		b, err := Load(string(doc))
		if err != nil {
			fmt.Println("Load:", err)
			return 1
		}
		if _, err := b.Start(0); err != nil {
			fmt.Println("Start:", err)
			return 1
		}
	case "signals": // starting the runtime leaves the signal dispositions of the process as they were
		ign, cgt := procStatus("SigIgn"), procStatus("SigCgt")
		if err := Init(); err != nil {
			fmt.Println("Init:", err)
			return 1
		}
		if ign2, cgt2 := procStatus("SigIgn"), procStatus("SigCgt"); ign2 != ign || cgt2 != cgt {
			fmt.Printf("signals changed: SigIgn %s → %s, SigCgt %s → %s\n", ign, ign2, cgt, cgt2)
			return 1
		}
	case "rlimit": // the runtime starts with little address space above the current usage
		kb, _ := strconv.ParseUint(strings.Fields(procStatus("VmSize") + " 0")[0], 10, 64)
		lim := syscall.Rlimit{Cur: kb<<10 + 256<<20, Max: kb<<10 + 256<<20}
		if err := syscall.Setrlimit(syscall.RLIMIT_AS, &lim); err != nil {
			fmt.Println("setrlimit:", err)
			return 3
		}
		b, err := Load(string(doc))
		if err != nil {
			fmt.Println("Load under RLIMIT_AS:", err)
			return 1
		}
		if _, err := b.Start(0); err != nil {
			fmt.Println("Start under RLIMIT_AS:", err)
			return 1
		}
	default:
		fmt.Println("unknown mode", mode)
		return 3
	}
	fmt.Println("ok")
	return 0
}

func runChild(t *testing.T, mode string) {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^$")
	cmd.Env = append(os.Environ(), "FIGRAM_TEST_CHILD="+mode)
	out, err := cmd.CombinedOutput()
	if err != nil || strings.TrimSpace(string(out)) != "ok" {
		t.Fatalf("child %s: %v\n%s", mode, err, out)
	}
}

func TestLazyInit(t *testing.T) { runChild(t, "lazy") }

func TestInitKeepsSignals(t *testing.T) { runChild(t, "signals") }

func TestInitUnderRlimitAS(t *testing.T) {
	if raceEnabled {
		t.Skip("the race detector reserves too much address space")
	}
	runChild(t, "rlimit")
}
