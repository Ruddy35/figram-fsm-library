// System sizes for the Lean runtime. By default the runtime reserves 1 GiB of address space for
// the stack of each of its threads and the mimalloc allocator reserves a 1 GiB arena; about 10 MB
// is actually used, but such reservations do not fit under `ulimit -v` (RLIMIT_AS). The Figram
// runtime starts no service threads (ffi/figram_init.cpp); a thread with a runtime stack exists
// only if the user sets LEAN_MAIN_USE_THREAD=1 for an executable. Here the runtime thread stack
// becomes the same as that of any thread of the process (RLIMIT_STACK, `ulimit -s`).
// mimalloc gets a 64 MiB arena (its smallest reservation) if at least 128 MiB of address space is
// free, and further arenas of 64 MiB as needed. Without an arena, memory freed after a large
// request (a snapshot with thousands of waits) is not reused: the resident memory grew by about
// 1.5 bytes per wait with every such request, without bound (tests/engine/engine_memory.py,
// tests/capi/contract.c large_rss). Under a tighter RLIMIT_AS mimalloc reserves no arena (memory is
// taken from the OS as needed): a fixed reservation of 64 MiB would end the runtime with
// "INTERNAL PANIC: out of memory" there. Explicit LEAN_STACK_SIZE_KB and MIMALLOC_ARENA_RESERVE
// take precedence.
// Call before lean_initialize_runtime_module(): figram_init (ffi/figram_ffi.c) and the constructor
// of the executables (ffi/figram_exe.c).
#include <lean/mimalloc.h>

/* Declarations from libc and the runtime: leanc of the official Lean release compiles with -nostdinc. */
char* getenv(const char* name);
int getrlimit(int resource, void* rlim);
void* mmap(void* addr, size_t len, int prot, int flags, int fd, long off);
int munmap(void* addr, size_t len);
void* lean_internal_set_thread_stack_size(size_t sz);
#define FG_RLIMIT_STACK 3

#define FG_DEFAULT_STACK (8ul << 20)    /* as in glibc when RLIMIT_STACK is not set */
#define FG_ARENA_RESERVE_KIB (64ul << 10)
#define FG_ARENA_PROBE (128ul << 20)          /* the arena and as much again for everything else */
#define FG_MAP_PRIVATE_ANON_NORESERVE (0x02 | 0x20 | 0x4000)   /* Linux: MAP_PRIVATE|MAP_ANONYMOUS|MAP_NORESERVE */

/* At least `n` bytes of address space are free (RLIMIT_AS): a mapping without access, released. */
static int fg_address_space_free(size_t n) {
  void* p = mmap((void*)0, n, 0 /* PROT_NONE */, FG_MAP_PRIVATE_ANON_NORESERVE, -1, 0);
  if (p == (void*)-1) return 0;
  munmap(p, n);
  return 1;
}

void figram_rt_defaults(void) {
  if (!getenv("LEAN_STACK_SIZE_KB")) {
    unsigned long lim[2] = {0, 0};                     /* struct rlimit: cur, max */
    unsigned long sz = FG_DEFAULT_STACK;
    if (getrlimit(FG_RLIMIT_STACK, lim) == 0 && lim[0] != (unsigned long)-1 && lim[0] >= (1ul << 20))
      sz = lim[0];
    lean_internal_set_thread_stack_size(sz);
  }
  if (!getenv("MIMALLOC_ARENA_RESERVE") && !getenv("mimalloc_arena_reserve")) {
    mi_option_set(mi_option_arena_reserve, 0);        /* no default 1 GiB arena meanwhile */
    if (fg_address_space_free(FG_ARENA_PROBE) && mi_reserve_os_memory(FG_ARENA_RESERVE_KIB << 10, false, false) == 0)
      mi_option_set(mi_option_arena_reserve, FG_ARENA_RESERVE_KIB);
  }
}
