// Сколько памяти занимает встроенная библиотека Figram в процессе Go (RSS и резерв адресов).
package main

/*
#include <stdlib.h>
#include "figram.h"
*/
import "C"

import (
	"bufio"
	"fmt"
	"os"
	"runtime"
	"strings"
	"unsafe"
)

func mem() (rss, vsz int) {
	f, _ := os.Open("/proc/self/status")
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		var v int
		if n, _ := fmt.Sscanf(s.Text(), "VmRSS: %d kB", &v); n == 1 {
			rss = v
		}
		if n, _ := fmt.Sscanf(s.Text(), "VmSize: %d kB", &v); n == 1 {
			vsz = v
		}
	}
	return
}

var huge int

func privDirty() int {
	pd := -1
	f, _ := os.Open("/proc/self/smaps_rollup")
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		var v int
		if n, _ := fmt.Sscanf(s.Text(), "Private_Dirty: %d kB", &v); n == 1 {
			pd = v
		}
		if n, _ := fmt.Sscanf(s.Text(), "AnonHugePages: %d kB", &v); n == 1 {
			huge = v
		}
	}
	return pd
}

func show(stage string, base int) int {
	runtime.GC()
	rss, vsz := mem()
	pd := privDirty()
	fmt.Printf("%-44s RSS %7d КБ (+%6d КБ к началу), своих данных %6d КБ (из них огромных страниц %5d КБ), адресов %8d КБ\n", stage, rss, rss-base, pd, huge, vsz)
	return rss
}

// dumpMaps: крупнейшие области по своим изменённым данным (Private_Dirty).
func dumpMaps() {
	f, _ := os.Open("/proc/self/smaps")
	defer f.Close()
	sc := bufio.NewScanner(f)
	type area struct{ name, perm string; size, dirty int }
	var cur *area
	var all []*area
	for sc.Scan() {
		fs := strings.Fields(sc.Text())
		if len(fs) >= 5 && strings.Contains(fs[0], "-") && !strings.HasSuffix(fs[0], ":") {
			var a, b uint64
			fmt.Sscanf(fs[0], "%x-%x", &a, &b)
			name := "(анонимная)"
			if len(fs) > 5 { name = fs[5] }
			cur = &area{name, fs[1], int((b - a) >> 10), 0}
			all = append(all, cur)
		} else if len(fs) >= 2 && fs[0] == "Private_Dirty:" && cur != nil {
			fmt.Sscanf(fs[1], "%d", &cur.dirty)
		}
	}
	for i := 0; i < len(all); i++ { for j := i + 1; j < len(all); j++ { if all[j].dirty > all[i].dirty { all[i], all[j] = all[j], all[i] } } }
	for _, a := range all[:8] { fmt.Printf("    своих %6d КБ в области %8d КБ %s %s\n", a.dirty, a.size, a.perm, a.name) }
}

func main() {
	doc, _ := os.ReadFile(os.Args[1])
	base, _ := mem()
	show("старт процесса Go", base)
	if rc := C.figram_init(); rc != 0 {
		fmt.Println("figram_init:", rc, C.GoString(C.figram_last_error()))
		os.Exit(1)
	}
	afterInit := show("после figram_init (рантайм Lean)", base)
	if os.Getenv("GOMEM_MAPS") != "" {
		dumpMaps()
	}
	cdoc := C.CString(string(doc))
	defer C.free(unsafe.Pointer(cdoc))
	ms := []unsafe.Pointer{C.figram_load_doc(cdoc)}
	one := show("загружен 1 автомат (pizza)", base)
	for i := 1; i < 1000; i++ {
		ms = append(ms, C.figram_load_doc(cdoc))
	}
	thousand := show("загружено 1000 автоматов", base)
	fmt.Printf("  ≈ %d КБ на автомат; рантайм Lean ≈ %d КБ\n", (thousand-one)/999, afterInit-base)
	req := C.CString(`{"id":1,"op":"step","snapshot":{"at":"menu","seq":1},"event":"btn:pizza"}`)
	defer C.free(unsafe.Pointer(req))
	for i := 0; i < 200000; i++ {
		r := C.figram_request_json(ms[i%len(ms)], req)
		if i == 0 && !strings.Contains(C.GoString(r), `"snapshot"`) {
			fmt.Println("неожиданный ответ:", C.GoString(r))
		}
		C.figram_free(r)
	}
	show("после 200 000 запросов", base)
	for _, m := range ms {
		C.figram_unload(m)
	}
	show("после выгрузки 1000 автоматов", base)
}
