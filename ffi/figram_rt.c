// System sizes for the Lean runtime. By default the runtime reserves 1 GiB of address space for
// the stack of each of its threads and the mimalloc allocator reserves a 1 GiB arena; about 10 MB
// is actually used, but such reservations do not fit under `ulimit -v` (RLIMIT_AS). The Figram
// runtime starts no service threads (ffi/figram_init.cpp); a thread with a runtime stack exists
// only if the user sets LEAN_MAIN_USE_THREAD=1 for an executable. Here the runtime thread stack
// becomes the same as that of any thread of the process (RLIMIT_STACK, `ulimit -s`), and mimalloc
// reserves no arena in advance (arena_reserve = 0: memory is taken from the OS as needed; mimalloc
// rounds any non-zero reservation up to 64 MiB, and with a few MiB of headroom the runtime would
// die with "INTERNAL PANIC: out of memory"). Explicit LEAN_STACK_SIZE_KB and MIMALLOC_ARENA_RESERVE
// take precedence.
// Call before lean_initialize_runtime_module(): figram_init (ffi/figram_ffi.c) and the constructor
// of the executables (ffi/figram_exe.c).
#include <lean/mimalloc.h>

/* Declarations from libc and the runtime: leanc of the official Lean release compiles with -nostdinc. */
char* getenv(const char* name);
int getrlimit(int resource, void* rlim);
void* lean_internal_set_thread_stack_size(size_t sz);
#define FG_RLIMIT_STACK 3

#define FG_DEFAULT_STACK (8ul << 20)    /* as in glibc when RLIMIT_STACK is not set */
#define FG_ARENA_RESERVE_KIB 0

void figram_rt_defaults(void) {
  if (!getenv("LEAN_STACK_SIZE_KB")) {
    unsigned long lim[2] = {0, 0};                     /* struct rlimit: cur, max */
    unsigned long sz = FG_DEFAULT_STACK;
    if (getrlimit(FG_RLIMIT_STACK, lim) == 0 && lim[0] != (unsigned long)-1 && lim[0] >= (1ul << 20))
      sz = lim[0];
    lean_internal_set_thread_stack_size(sz);
  }
  if (!getenv("MIMALLOC_ARENA_RESERVE") && !getenv("mimalloc_arena_reserve"))
    mi_option_set(mi_option_arena_reserve, FG_ARENA_RESERVE_KIB);
}
