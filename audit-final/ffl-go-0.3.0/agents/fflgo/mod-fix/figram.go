package figram

/*
#cgo CFLAGS: -I${SRCDIR}
#include <stdlib.h>
#include <string.h>
#include "figram.h"

// figram_last_error() is thread-local: it is copied in the same C call, because a goroutine
// may move to another OS thread between two cgo calls.
static int fg_init(char* buf, size_t cap) {
	int rc = figram_init();
	buf[0] = 0;
	if (rc != 0) {
		const char* e = figram_last_error();
		size_t n = e ? strlen(e) : 0;
		if (n >= cap) n = cap - 1;
		if (n) memcpy(buf, e, n);
		buf[n] = 0;
	}
	return rc;
}
*/
import "C"

import (
	"errors"
	"fmt"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"unsafe"
)

var (
	initOnce sync.Once
	initErr  error
)

// Init starts the Lean runtime of the engine, once per process. Every other function calls it
// itself; call it explicitly to learn the result early. A failure is final and is returned by
// every later call, for example when RLIMIT_AS leaves the runtime too little address space.
//
// Starting the runtime creates no threads, file descriptors or signal handlers; it only sets the
// disposition of SIGPIPE and restores it.
func Init() error {
	initOnce.Do(func() {
		buf := (*C.char)(C.malloc(1024))
		defer C.free(unsafe.Pointer(buf))
		if rc := C.fg_init(buf, 1024); rc != 0 {
			initErr = fmt.Errorf("figram: the engine runtime did not start (figram_init = %d): %s", int(rc), C.GoString(buf))
		}
	})
	return initErr
}

// SetMaxRequest sets the request size limit in bytes for the whole process (0: no limit) and
// returns the previous limit. The default is 1 MiB, as in figram-engine. A longer request fails
// with a [ProtocolError] with code [CodeTooLarge].
func SetMaxRequest(n int) int {
	if n < 0 {
		n = 0
	}
	return int(C.figram_set_max_request(C.size_t(n)))
}

// DocumentError lists why a document was rejected by [Load], [Check] or [Fmt].
type DocumentError struct {
	Problems []string // one entry per problem, as figram check prints them
}

func (e *DocumentError) Error() string {
	return "figram: document rejected: " + strings.Join(e.Problems, "; ")
}

// ErrClosed is returned by the methods of a [Bot] after [Bot.Close].
var ErrClosed = errors.New("figram: bot is closed")

// cBytes returns a pointer to the bytes of a Go string for a C call with an explicit length:
// the memory holds no Go pointers, and the library only reads it and does not keep it (the cgo
// rules allow this).
func cBytes(s string) (*C.char, C.size_t) {
	if len(s) == 0 {
		return nil, 0
	}
	return (*C.char)(unsafe.Pointer(unsafe.StringData(s))), C.size_t(len(s))
}

// docProblems splits a document report of the library: "error: <reason>" for a text that is not
// a document (parse error, invalid UTF-8), otherwise one problem per line.
func docProblems(s string) []string {
	if r, ok := strings.CutPrefix(s, "error: "); ok {
		return []string{r}
	}
	return strings.Split(s, "\n")
}

// takeString copies a string returned by the library and frees it.
func takeString(p *C.char) (string, error) {
	if p == nil {
		return "", errors.New("figram: out of memory")
	}
	defer C.figram_free(p)
	return C.GoString(p), nil
}

func checkDoc(doc string) error {
	if err := Init(); err != nil {
		return err
	}
	// The library rejects an embedded NUL itself; this gives a clear message.
	if strings.IndexByte(doc, 0) >= 0 {
		return &DocumentError{Problems: []string{"the document contains a NUL byte"}}
	}
	return nil
}

// Check checks a document as figram check does. It returns nil for a valid document and a
// [*DocumentError] with the problems otherwise. Warnings of figram check are not reported.
func Check(doc string) error {
	if err := checkDoc(doc); err != nil {
		return err
	}
	p, n := cBytes(doc)
	out, err := takeString(C.figram_check_json_n(p, n))
	runtime.KeepAlive(doc)
	if err != nil {
		return err
	}
	if out == "ok" {
		return nil
	}
	return &DocumentError{Problems: docProblems(out)}
}

// Fmt returns the canonical form of a document, as figram fmt prints it (without the final
// newline). It reformats documents that [Check] rejects too, as long as they are documents.
func Fmt(doc string) (string, error) {
	if err := checkDoc(doc); err != nil {
		return "", err
	}
	p, n := cBytes(doc)
	out, err := takeString(C.figram_fmt_json_n(p, n))
	runtime.KeepAlive(doc)
	if err != nil {
		return "", err
	}
	if strings.HasPrefix(out, "error: ") {
		return "", &DocumentError{Problems: docProblems(out)}
	}
	return out, nil
}

// Bot is a loaded document: the compiled machine of one bot. It keeps no users: every request
// carries the user's [Snapshot]. A Bot is safe for concurrent use by many goroutines; one Bot
// serves all users of the bot.
type Bot struct {
	mu     sync.RWMutex
	m      unsafe.Pointer
	nextID atomic.Int64
}

// Load checks and compiles a document. A rejected document gives a [*DocumentError]. Load also
// fails if the library speaks another protocol version than [ProtocolVersion].
func Load(doc string) (*Bot, error) {
	if err := checkDoc(doc); err != nil {
		return nil, err
	}
	p, n := cBytes(doc)
	var cerr *C.char
	m := C.figram_load_doc_ex_n(p, n, &cerr)
	runtime.KeepAlive(doc)
	if m == nil {
		if cerr == nil {
			return nil, errors.New("figram: out of memory")
		}
		reason, _ := takeString(cerr)
		return nil, &DocumentError{Problems: docProblems(reason)}
	}
	b := &Bot{m: m}
	runtime.SetFinalizer(b, (*Bot).Close)
	if err := b.checkProtocol(); err != nil {
		b.Close()
		return nil, err
	}
	return b, nil
}

// checkProtocol rejects a library that speaks another protocol version than this package (with
// the build tag figram_system the library may be of another release).
func (b *Bot) checkProtocol() error {
	h, err := b.Hello()
	if err != nil {
		return err
	}
	return protocolMismatch(h.Protocol)
}

func protocolMismatch(p int) error {
	if p != ProtocolVersion {
		return fmt.Errorf("figram: the library speaks protocol %d, this package needs %d (Figram %s)",
			p, ProtocolVersion, BundledVersion)
	}
	return nil
}

// Close frees the machine after the running requests finish. Later requests fail with
// [ErrClosed]. Close may be called more than once.
func (b *Bot) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.m != nil {
		C.figram_unload(b.m)
		b.m = nil
		runtime.SetFinalizer(b, nil)
	}
}

// Request sends one raw request line of the engine protocol (docs/format.md, section 8, in the
// Figram repository) and returns the reply line as the engine wrote it, without checking it. The
// typed methods ([Bot.Start], [Bot.Step], ...) are built on it.
func (b *Bot) Request(line []byte) ([]byte, error) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.m == nil {
		return nil, ErrClosed
	}
	buf := line
	if len(buf) == 0 {
		buf = []byte{0} // a valid pointer: the library answers an empty request with parse_error
	}
	out := C.figram_request_json_n(b.m, (*C.char)(unsafe.Pointer(&buf[0])), C.size_t(len(line)))
	runtime.KeepAlive(buf)
	if out == nil {
		return nil, errors.New("figram: out of memory")
	}
	defer C.figram_free(out)
	return C.GoBytes(unsafe.Pointer(out), C.int(C.strlen(out))), nil
}
