// fgprobe: small native-path probes of the embedded engine (used by the audit report).
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"figramhost/figram"
)

func threads() string {
	ents, _ := os.ReadDir("/proc/self/task")
	var names []string
	for _, e := range ents {
		b, _ := os.ReadFile("/proc/self/task/" + e.Name() + "/comm")
		names = append(names, strings.TrimSpace(string(b)))
	}
	st, _ := os.ReadFile("/proc/self/status")
	vm := ""
	for _, l := range strings.Split(string(st), "\n") {
		if strings.HasPrefix(l, "VmSize") || strings.HasPrefix(l, "VmRSS") {
			vm += strings.Join(strings.Fields(l), " ") + "; "
		}
	}
	return fmt.Sprintf("%d threads %v %s", len(names), names, vm)
}

func main() {
	fmt.Println("before init:", threads())
	figram.Init()
	time.Sleep(200 * time.Millisecond)
	fmt.Println("after init: ", threads())
	doc, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	m, err := figram.Load("pizza", doc)
	if err != nil {
		panic(err)
	}
	fmt.Printf("init: %+v load=%v\n", figram.InitReport(), m.LoadTime)
	try := func(name string, line []byte) {
		r, err := m.Request(line)
		fmt.Printf("%-22s err=%v reply=%q\n", name, err, r)
	}
	try("hello", []byte(`{"id":1,"op":"hello"}`))
	try("start", []byte(`{"id":2,"op":"start"}`))
	try("nul-raw", []byte("{\"id\":3,\"op\":\"hello\"}\x00garbage"))
	try("nul-escaped-resume", []byte(`{"id":4,"op":"step","snapshot":{"at":"menu","resume":{"a\u0000b":"c"}},"event":"btn:none"}`))
	try("bad-utf8-resume", []byte("{\"id\":5,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"resume\":{\"k\xff\xfe\":\"v\"}},\"event\":\"btn:none\"}"))
	try("bad-utf8-at", []byte("{\"id\":6,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\xff\"},\"event\":\"btn:order\"}"))
	try("bad-utf8-id", []byte("{\"id\":\"\xff\",\"op\":\"hello\"}"))
	try("lone-surrogate", []byte(`{"id":7,"op":"step","snapshot":{"at":"menu","resume":{"\ud800":"c"}},"event":"btn:none"}`))
	try("empty", []byte(``))
	try("guard-nul-name", []byte(`{"id":8,"op":"step","snapshot":{"at":"cart"},"event":"btn:checkout","guards":{"x\u0000y":1}}`))
	b, _ := json.Marshal(map[string]any{"id": 9, "op": "step", "snapshot": map[string]any{"at": "menu"}, "event": "btn:\xff"})
	try("go-json-marshal-badutf8", b)
	_, err = figram.Load("bad", []byte(`{"figram":1,"bot":{"initial":"x","children":{"y":{}}}}`))
	fmt.Println("load-invalid:", err)
	_, err = figram.Load("nul", []byte("{\"figram\":1}\x00"))
	fmt.Println("load-nul:", err)
	m.Close()
	_, err = m.Request([]byte(`{"id":1,"op":"hello"}`))
	fmt.Println("after-close:", err)
	fmt.Printf("stats: %+v\n", figram.GetStats())
}
