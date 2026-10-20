// Package figram is an independent cgo binding to the embedded Figram engine
// (libfigram.a: ffi/figram_ffi.c + Figram/Api.lean, Lean 4 runtime linked statically).
//
// Ownership, lifetime and threading decisions (audit host-go):
//
//   - Runtime init. figram_init() is not safe to call concurrently (plain static
//     flag in figram_ffi.c), so Init() wraps it in sync.Once and caches the result.
//     Every exported entry point calls Init() first. The Lean runtime init
//     (initialize_stack_overflow -> stack_guard) installs a malloc'ed SIGSTKSZ
//     (14.5 KiB on glibc 2.39) sigaltstack on the *calling OS thread*, which is a Go
//     M. Go would then run its signal handlers for that M on Lean's small stack.
//     We save the thread's sigaltstack before figram_init and restore it afterwards
//     (see fg_init below); InitReport() says whether Lean changed it.
//   - Threads. figram_ffi.c calls lean_initialize_thread() once per OS thread
//     (thread-local flag) on every entry, so any goroutine may call in on any M.
//     Every cgo call pins its M for the duration; to bound the number of OS threads
//     the Go runtime creates for concurrent cgo calls, calls are limited by a
//     package semaphore (SetMaxConcurrentCalls, default GOMAXPROCS).
//   - Strings. The C ABI takes NUL-terminated strings without a length: a raw NUL
//     byte would silently truncate the request on the C side. Request/Load/Check
//     therefore REJECT inputs that contain a NUL byte (ErrNUL). Invalid UTF-8 is
//     passed through (the Lean side replaces it with U+FFFD, see tests).
//     Inputs are copied into C memory (malloc) and freed after the call; replies are
//     malloc'ed by the C shim, copied into Go memory and released with figram_free
//     on every path (defer).
//   - Machine lifetime. A *Machine owns one reference to the Lean object returned by
//     figram_load_doc. Request() takes an in-flight reference under a mutex;
//     Close() marks the machine closing (new Requests fail with ErrClosed), waits
//     until every in-flight Request has returned from C, then calls figram_unload
//     exactly once. figram_request_json itself does lean_inc on entry, so a call that
//     has entered C would survive an unload; the in-flight count protects the window
//     before that inc and any call that starts after the unload (use-after-free).
//   - No finalizers: unloading is explicit. A leaked *Machine leaks the compiled
//     automaton (by design: the GC cannot know about in-flight C calls).
package figram

/*
#cgo LDFLAGS: -L${SRCDIR}/../../../ffi/out -l:libfigram.a -L${SRCDIR}/../../../ffi/out/lean -Wl,--start-group -lInit -lleanrt -Wl,--end-group -luv -lgmp -lstdc++ -lm -ldl -lpthread -lrt
#include <stdlib.h>
#include <string.h>
#include <signal.h>

int   figram_init(void);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
char* figram_check_json(const char*);
void  figram_unload(void*);
void  figram_free(char*);

// fg_init calls figram_init and (optionally) gives the calling Go M its signal
// stack back. *changed = 1 if the Lean runtime replaced the thread's sigaltstack.
static int fg_noop(int x) { return x + 1; }

static int fg_init(int restore, int* changed, long* lean_ss_size) {
	stack_t before, after;
	memset(&before, 0, sizeof before);
	memset(&after, 0, sizeof after);
	sigaltstack(NULL, &before);
	int r = figram_init();
	sigaltstack(NULL, &after);
	*changed = (after.ss_sp != before.ss_sp || after.ss_size != before.ss_size);
	*lean_ss_size = (long)after.ss_size;
	if (*changed && restore) sigaltstack(&before, NULL);
	return r;
}
*/
import "C"

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"runtime"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"
)

var (
	ErrClosed   = errors.New("figram: machine is closed")
	ErrNUL      = errors.New("figram: input contains a NUL byte (C ABI would truncate it)")
	ErrInit     = errors.New("figram: runtime initialisation failed")
	ErrNilReply = errors.New("figram: engine returned NULL")
)

// InitInfo describes what happened during runtime initialisation.
type InitInfo struct {
	Code              int  // figram_init return code (0 = ok)
	SigaltstackChange bool // Lean replaced the calling thread's sigaltstack
	LeanSigstackSize  int  // size of the stack Lean installed (bytes)
	Restored          bool // we restored the Go M's original sigaltstack
	Duration          time.Duration
}

var (
	initOnce sync.Once
	initErr  error
	initInfo InitInfo

	slots   chan struct{}
	slotsMu sync.Mutex

	stCalls    atomic.Int64
	stNanos    atomic.Int64
	stInBytes  atomic.Int64
	stOutBytes atomic.Int64
	stLoads    atomic.Int64
	stUnloads  atomic.Int64
	stLive     atomic.Int64
)

func init() {
	slots = make(chan struct{}, runtime.GOMAXPROCS(0))
}

// SetMaxConcurrentCalls bounds the number of simultaneous C calls (and so the
// number of OS threads pinned in C). Must be called before the first call.
func SetMaxConcurrentCalls(n int) {
	if n < 1 {
		n = 1
	}
	slotsMu.Lock()
	slots = make(chan struct{}, n)
	slotsMu.Unlock()
}

func acquireSlot() chan struct{} {
	slotsMu.Lock()
	s := slots
	slotsMu.Unlock()
	s <- struct{}{}
	return s
}

// Init initialises the Lean runtime once. Safe for concurrent use.
// FIGRAM_KEEP_LEAN_SIGALTSTACK=1 disables the sigaltstack restore (experiment only).
func Init() error {
	initOnce.Do(func() {
		restore := 1
		if os.Getenv("FIGRAM_KEEP_LEAN_SIGALTSTACK") == "1" {
			restore = 0
		}
		// The sigaltstack is per OS thread: keep this goroutine on one M while
		// we look at it before and after.
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		var changed C.int
		var ss C.long
		t0 := time.Now()
		r := C.fg_init(C.int(restore), &changed, &ss)
		initInfo = InitInfo{Code: int(r), SigaltstackChange: changed != 0, LeanSigstackSize: int(ss),
			Restored: changed != 0 && restore == 1, Duration: time.Since(t0)}
		if r != 0 {
			initErr = fmt.Errorf("%w: figram_init returned %d", ErrInit, int(r))
		}
	})
	return initErr
}

// InitReport returns what Init observed (zero value before Init).
func InitReport() InitInfo { return initInfo }

func cBytes(b []byte) (*C.char, error) {
	if bytes.IndexByte(b, 0) >= 0 {
		return nil, ErrNUL
	}
	p := (*C.char)(C.malloc(C.size_t(len(b) + 1)))
	if p == nil {
		return nil, errors.New("figram: malloc failed")
	}
	if len(b) > 0 {
		copy(unsafe.Slice((*byte)(unsafe.Pointer(p)), len(b)), b)
	}
	*(*byte)(unsafe.Add(unsafe.Pointer(p), len(b))) = 0
	return p, nil
}

func takeReply(r *C.char) ([]byte, error) {
	if r == nil {
		return nil, ErrNilReply
	}
	defer C.figram_free(r)
	n := C.strlen(r)
	out := C.GoBytes(unsafe.Pointer(r), C.int(n))
	return out, nil
}

// Check runs figram_check_json: "ok" or the error list (one per line).
func Check(doc []byte) (string, error) {
	if err := Init(); err != nil {
		return "", err
	}
	cs, err := cBytes(doc)
	if err != nil {
		return "", err
	}
	defer C.free(unsafe.Pointer(cs))
	s := acquireSlot()
	defer func() { <-s }()
	out, err := takeReply(C.figram_check_json(cs))
	return string(out), err
}

// Machine is a loaded, compiled bot document. Safe for concurrent Request calls.
type Machine struct {
	mu       sync.Mutex
	idle     *sync.Cond
	ptr      unsafe.Pointer
	inflight int
	closing  bool
	closed   bool
	Name     string
	LoadTime time.Duration
}

// Load compiles a document. On failure the reason comes from figram_check_json
// (figram_load_doc itself only returns NULL).
func Load(name string, doc []byte) (*Machine, error) {
	if err := Init(); err != nil {
		return nil, err
	}
	cs, err := cBytes(doc)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(cs))
	s := acquireSlot()
	t0 := time.Now()
	p := C.figram_load_doc(cs)
	dt := time.Since(t0)
	<-s
	if p == nil {
		why, _ := Check(doc)
		return nil, fmt.Errorf("figram: document %q rejected: %s", name, why)
	}
	stLoads.Add(1)
	stLive.Add(1)
	m := &Machine{ptr: p, Name: name, LoadTime: dt}
	m.idle = sync.NewCond(&m.mu)
	return m, nil
}

func (m *Machine) enter() (unsafe.Pointer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closing || m.ptr == nil {
		return nil, ErrClosed
	}
	m.inflight++
	return m.ptr, nil
}

func (m *Machine) leave() {
	m.mu.Lock()
	m.inflight--
	if m.inflight == 0 {
		m.idle.Broadcast()
	}
	m.mu.Unlock()
}

// Request sends one protocol line (FORMAT.md §7) and returns the reply line.
func (m *Machine) Request(line []byte) ([]byte, error) {
	cs, err := cBytes(line)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(cs))
	p, err := m.enter()
	if err != nil {
		return nil, err
	}
	defer m.leave()
	s := acquireSlot()
	defer func() { <-s }()
	t0 := time.Now()
	r := C.figram_request_json(p, cs)
	stNanos.Add(int64(time.Since(t0)))
	stCalls.Add(1)
	stInBytes.Add(int64(len(line)))
	out, err := takeReply(r)
	stOutBytes.Add(int64(len(out)))
	return out, err
}

// Close stops new requests, waits for in-flight ones and unloads the automaton.
// Idempotent; safe to call concurrently with Request.
func (m *Machine) Close() {
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return
	}
	m.closing = true
	for m.inflight > 0 {
		m.idle.Wait()
	}
	p := m.ptr
	m.ptr = nil
	m.closed = true
	m.mu.Unlock()
	if p != nil {
		s := acquireSlot()
		C.figram_unload(p)
		<-s
		stUnloads.Add(1)
		stLive.Add(-1)
	}
}

// CloseUnsafeNoWait unloads immediately WITHOUT waiting for in-flight calls and
// without blocking new ones. Only for the deliberately broken host variant
// (shows that the in-flight guard is load-bearing). Never use otherwise.
func (m *Machine) CloseUnsafeNoWait() {
	m.mu.Lock()
	p := m.ptr
	m.closed = true
	m.mu.Unlock()
	if p != nil {
		C.figram_unload(p)
		stUnloads.Add(1)
		stLive.Add(-1)
	}
}

// RequestUnsafe calls into C with the raw pointer and no in-flight accounting
// (broken variant companion of CloseUnsafeNoWait).
func (m *Machine) RequestUnsafe(line []byte) ([]byte, error) {
	cs, err := cBytes(line)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(cs))
	m.mu.Lock()
	p := m.ptr
	m.mu.Unlock()
	if p == nil {
		return nil, ErrClosed
	}
	return takeReply(C.figram_request_json(p, cs))
}

// Inflight returns the number of requests currently inside C for this machine.
func (m *Machine) Inflight() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.inflight
}

// Stats are process-wide binding counters.
type Stats struct {
	Calls, Nanos, InBytes, OutBytes, Loads, Unloads, Live int64
}

func GetStats() Stats {
	return Stats{stCalls.Load(), stNanos.Load(), stInBytes.Load(), stOutBytes.Load(),
		stLoads.Load(), stUnloads.Load(), stLive.Load()}
}

// cgoNoop measures the bare cgo transition cost (benchmarks only).
func cgoNoop(x int) int { return int(C.fg_noop(C.int(x))) }
