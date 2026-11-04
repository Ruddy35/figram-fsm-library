// Package rawfg is a minimal cgo binding of ffi/figram.h written for this audit (independent of
// clients/go): raw bytes in and out, no locking, no validation. It is used to measure the
// engine without the wrapper's JSON work and to probe the C ABI contract (unload while held).
package rawfg

/*
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include "figram.h"
static size_t fg_heap_inuse(void) { struct mallinfo2 m = mallinfo2(); return m.uordblks; }
static size_t fg_heap_arena(void) { struct mallinfo2 m = mallinfo2(); return m.arena + m.hblkhd; }
static int fg_load(const char* d, size_t n, void** out, char* buf, size_t cap) {
	char* e = NULL;
	void* m = figram_load_doc_ex_n(d, n, &e);
	*out = m;
	buf[0] = 0;
	if (!m && e) { size_t k = strlen(e); if (k >= cap) k = cap - 1; memcpy(buf, e, k); buf[k] = 0; figram_free(e); }
	return m != NULL;
}
*/
import "C"

import (
	"errors"
	"runtime"
	"unsafe"
)

type Handle struct{ p unsafe.Pointer }

func Init() int { return int(C.figram_init()) }

func Load(doc []byte) (Handle, error) {
	if len(doc) == 0 {
		return Handle{}, errors.New("empty")
	}
	buf := (*C.char)(C.malloc(4096))
	defer C.free(unsafe.Pointer(buf))
	var m unsafe.Pointer
	ok := C.fg_load((*C.char)(unsafe.Pointer(&doc[0])), C.size_t(len(doc)), &m, buf, 4096)
	runtime.KeepAlive(doc)
	if ok == 0 {
		return Handle{}, errors.New(C.GoString(buf))
	}
	return Handle{m}, nil
}

// Request sends one request line and returns the reply bytes (copied).
func Request(h Handle, line []byte) []byte {
	var p *C.char
	if len(line) > 0 {
		p = (*C.char)(unsafe.Pointer(&line[0]))
	}
	out := C.figram_request_json_n(h.p, p, C.size_t(len(line)))
	runtime.KeepAlive(line)
	if out == nil {
		return nil
	}
	defer C.figram_free(out)
	return C.GoBytes(unsafe.Pointer(out), C.int(C.strlen(out)))
}

func Unload(h Handle) { C.figram_unload(h.p) }

func SetMaxRequest(n int) int { return int(C.figram_set_max_request(C.size_t(n))) }

func Check(doc []byte) string {
	out := C.figram_check_json_n((*C.char)(unsafe.Pointer(&doc[0])), C.size_t(len(doc)))
	runtime.KeepAlive(doc)
	defer C.figram_free(out)
	return C.GoString(out)
}

// MallocInUse: bytes in use by glibc malloc (strings returned by the library, Lean big objects
// if they go through malloc). The Lean small-object heap is not visible here.
func MallocInUse() uint64 { return uint64(C.fg_heap_inuse()) }
func MallocArena() uint64 { return uint64(C.fg_heap_arena()) }
