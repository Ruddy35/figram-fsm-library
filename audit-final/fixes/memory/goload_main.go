// Память встроенной библиотеки Figram в процессе Go под нагрузкой.
//
//	goload base|fig DOC GOROUTINES BOTS SECONDS
//
// base — контроль: те же горутины, тот же вызов C и те же строки, но без Figram (пустая функция C);
// разница fig − base — цена библиотеки. Для каждой стадии: RSS (из него анонимная память и страницы файлов),
// Private_Dirty, резерв адресов (VmSize), «обязательство» (сумма частных записываемых областей — столько
// ядро учитывает в Committed_AS) и потоки ОС; под нагрузкой — пик опроса каждые 200 мс.
package main

/*
#include <stdlib.h>
#include <string.h>
#include "figram.h"
static char* echo(const char* s) { return strdup(s); }
*/
import "C"

import (
	"bufio"
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"
)

type st struct{ rss, anon, file, priv, vm, commit, thr int }

func stat() (s st) {
	f, _ := os.Open("/proc/self/status")
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		k, v, _ := strings.Cut(sc.Text(), ":")
		n, _ := strconv.Atoi(strings.Fields(v + " 0")[0])
		switch k {
		case "VmRSS":
			s.rss = n
		case "RssAnon":
			s.anon = n
		case "RssFile":
			s.file = n
		case "VmSize":
			s.vm = n
		case "Threads":
			s.thr = n
		}
	}
	f.Close()
	f, _ = os.Open("/proc/self/smaps_rollup")
	sc = bufio.NewScanner(f)
	for sc.Scan() {
		if fs := strings.Fields(sc.Text()); len(fs) >= 2 && fs[0] == "Private_Dirty:" {
			s.priv, _ = strconv.Atoi(fs[1])
		}
	}
	f.Close()
	f, _ = os.Open("/proc/self/smaps")
	sc = bufio.NewScanner(f)
	perm := ""
	for sc.Scan() {
		fs := strings.Fields(sc.Text())
		if len(fs) >= 5 && strings.Contains(fs[0], "-") && !strings.HasSuffix(fs[0], ":") {
			perm = fs[1]
		} else if len(fs) >= 2 && fs[0] == "Size:" && strings.Contains(perm, "w") && strings.HasSuffix(perm, "p") {
			n, _ := strconv.Atoi(fs[1])
			s.commit += n
		}
	}
	f.Close()
	return
}

func mb(kb int) float64 { return float64(kb) / 1024 }

func show(label string, s st) {
	fmt.Printf("%-40s RSS %6.1f МБ (анонимная %5.1f, файлов %4.1f), Private_Dirty %6.1f МБ, резерв %7.1f МБ, обязательство %6.1f МБ, потоков %d\n",
		label, mb(s.rss), mb(s.anon), mb(s.file), mb(s.priv), mb(s.vm), mb(s.commit), s.thr)
}

func main() {
	mode, docPath := os.Args[1], os.Args[2]
	g, _ := strconv.Atoi(os.Args[3])
	bots, _ := strconv.Atoi(os.Args[4])
	secs, _ := strconv.Atoi(os.Args[5])
	doc, _ := os.ReadFile(docPath)
	runtime.GC()
	show("старт", stat())
	cdoc := C.CString(string(doc))
	ms := make([]unsafe.Pointer, bots)
	if mode == "fig" {
		if rc := C.figram_init(); rc != 0 {
			fmt.Println("figram_init:", rc)
			os.Exit(1)
		}
		runtime.GC()
		show("после figram_init", stat())
		for i := range ms {
			if ms[i] = C.figram_load_doc(cdoc); ms[i] == nil {
				fmt.Println("load failed")
				os.Exit(1)
			}
		}
		runtime.GC()
		show(fmt.Sprintf("загружено автоматов: %d", bots), stat())
	}
	req := C.CString(`{"id":1,"op":"step","snapshot":{"at":"menu","seq":1},"event":"btn:pizza"}`)
	resp := C.CString(`{"id":1,"snapshot":{"at":"catalog","resume":{"order":"catalog"},"seq":2},"actions":[{"send":"catalog"}]}`)
	var n, bad atomic.Int64
	var peak st
	var mu sync.Mutex
	stop := make(chan struct{})
	sampled := make(chan struct{})
	go func() {
		defer close(sampled)
		for {
			select {
			case <-stop:
				return
			case <-time.After(200 * time.Millisecond):
				s := stat()
				mu.Lock()
				peak = st{max(peak.rss, s.rss), max(peak.anon, s.anon), max(peak.file, s.file), max(peak.priv, s.priv),
					max(peak.vm, s.vm), max(peak.commit, s.commit), max(peak.thr, s.thr)}
				mu.Unlock()
			}
		}
	}()
	deadline := time.Now().Add(time.Duration(secs) * time.Second)
	var wg sync.WaitGroup
	for i := 0; i < g; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			for k := i; time.Now().Before(deadline); k += g {
				var r *C.char
				if mode == "fig" {
					r = C.figram_request_json(ms[k%bots], req)
				} else {
					r = C.echo(resp)
				}
				if s := C.GoString(r); !strings.Contains(s, `"snapshot"`) {
					bad.Add(1)
				}
				if mode == "fig" {
					C.figram_free(r)
				} else {
					C.free(unsafe.Pointer(r))
				}
				n.Add(1)
			}
		}(i)
	}
	wg.Wait()
	close(stop)
	<-sampled
	fmt.Printf("нагрузка: %s, горутин %d, автоматов %d, %d с: запросов %d (%.0f/с), плохих ответов %d\n",
		mode, g, bots, secs, n.Load(), float64(n.Load())/float64(secs), bad.Load())
	show("пик под нагрузкой", peak)
	runtime.GC()
	show("после нагрузки", stat())
	if mode == "fig" {
		for _, m := range ms {
			C.figram_unload(m)
		}
		runtime.GC()
		show("после выгрузки автоматов", stat())
	}
	if os.Getenv("GOLOAD_MAPS") != "" {
		maps()
	}
	if bad.Load() != 0 {
		os.Exit(1)
	}
}

// maps: области памяти по группам — размер (резерв), записываемость, занято (Rss) и свои данные.
func maps() {
	f, _ := os.Open("/proc/self/smaps")
	defer f.Close()
	sc := bufio.NewScanner(f)
	type g struct{ n, size, rss, priv int }
	groups := map[string]*g{}
	var cur *g
	for sc.Scan() {
		fs := strings.Fields(sc.Text())
		if len(fs) >= 5 && strings.Contains(fs[0], "-") && !strings.HasSuffix(fs[0], ":") {
			var a, b uint64
			fmt.Sscanf(fs[0], "%x-%x", &a, &b)
			name := "анонимная"
			if len(fs) > 5 {
				name = fs[5]
			}
			sz := int((b - a) >> 10)
			kind := fmt.Sprintf("%-28s %s", name, fs[1])
			switch {
			case name == "анонимная" && fs[1] == "---p":
				kind = "анонимная ---p (только резерв)"
			case name == "анонимная" && sz == 8192:
				kind = "анонимная rw-p 8 МиБ (стек потока)"
			case name == "анонимная" && sz == 65536-132:
				kind = "анонимная rw-p (арена glibc потока)"
			}
			if groups[kind] == nil {
				groups[kind] = &g{}
			}
			cur = groups[kind]
			cur.n++
			cur.size += sz
		} else if len(fs) >= 2 && cur != nil {
			n, _ := strconv.Atoi(fs[1])
			switch fs[0] {
			case "Rss:":
				cur.rss += n
			case "Private_Dirty:":
				cur.priv += n
			}
		}
	}
	for k, v := range groups {
		if v.size >= 4096 || v.rss >= 512 {
			fmt.Printf("  %-44s областей %3d, размер %8.1f МБ, занято %6.1f МБ, своих %6.1f МБ\n", k, v.n, mb(v.size), mb(v.rss), mb(v.priv))
		}
	}
}
