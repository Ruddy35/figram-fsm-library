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

func show(stage string, base int) int {
	runtime.GC()
	rss, vsz := mem()
	fmt.Printf("%-44s RSS %7d КБ (+%6d КБ к началу)   адресов %8d КБ\n", stage, rss, rss-base, vsz)
	return rss
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
