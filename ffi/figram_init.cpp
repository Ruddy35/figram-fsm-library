// Starts the Lean runtime without the libuv event loop. This definition of
// lean_initialize_runtime_module replaces the one in libleanrt.a (src/runtime/init_module.cpp): the
// linker takes an object from an archive only if the symbol is not defined yet. The steps are those
// of Lean 4.35, except:
//  * initialize_libuv — libuv timers, sockets, signals and DNS and the permanent thread of their
//    event loop (with an io_uring SQPOLL kernel thread, about 8 file descriptors and a 64 MB glibc
//    malloc arena reservation). Only the functions of Std.Internal.UV and Std.Net use them, and
//    Figram programs have none; verify.sh proves this on the built files (ci/reach.py: the event
//    loop is unreachable from main and the constructors);
//  * initialize_stack_overflow — only in the embedded library (without FIGRAM_STACK_GUARD): the
//    SIGSEGV/SIGBUS handler that reports a stack overflow is installed for the whole process, and
//    the host's signals belong to the host. The executables figram and figram-engine keep it.
// verify.sh compares the sequence of calls with the definition in libleanrt.a of the installed Lean:
// a new initialization step in another Lean version stops the check instead of being skipped.
#include <lean/version.h>
#if LEAN_VERSION_MAJOR != 4 || LEAN_VERSION_MINOR != 35
#error "ffi/figram_init.cpp follows src/runtime/init_module.cpp of Lean 4.35: compare the steps with this Lean version"
#endif

namespace lean {
void initialize_alloc();
void initialize_debug();
void initialize_object();
void initialize_io();
void initialize_thread();
void initialize_mutex();
void initialize_process();
void initialize_stack_overflow();
}  // namespace lean

// Idempotent, as in Lean: both the generated main and figram_init call it.
extern "C" void lean_initialize_runtime_module() {
  static bool initialized = false;
  if (initialized) return;
  initialized = true;
  lean::initialize_alloc();
  lean::initialize_debug();
  lean::initialize_object();
  lean::initialize_io();
  lean::initialize_thread();
  lean::initialize_mutex();
  lean::initialize_process();
#ifdef FIGRAM_STACK_GUARD
  lean::initialize_stack_overflow();
#endif
}
