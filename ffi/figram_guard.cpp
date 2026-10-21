// C++ part of libfigram.a: calls a C function and catches C++ exceptions.
// The Lean runtime reports some failures with a C++ exception (lean::exception, std::bad_alloc).
// Through the C frames of figram_init the exception would reach the host, and std::terminate
// would end the whole process (SIGABRT). figram_guard_call catches it and returns its text. This
// used to be "failed to create thread" (the libuv thread stack under RLIMIT_AS); with
// ffi/figram_init.cpp the runtime start creates no threads, and Lean 4.35 has no known cause of
// an exception during start-up.
//
// No system headers: leanc of the official Lean release compiles with -nostdinc (only clang's
// built-in headers are available), so without <exception> std::exception is declared here. The
// declaration matches libstdc++ and libc++ under the Itanium ABI (a class in the unversioned std
// namespace with virtual ~exception and what).
#if defined(__has_include)
#if __has_include(<exception>)
#include <exception>
#define FIGRAM_HAVE_EXCEPTION_HEADER 1
#endif
#endif
#ifndef FIGRAM_HAVE_EXCEPTION_HEADER
namespace std {
class exception {
 public:
  virtual ~exception() noexcept;
  virtual const char* what() const noexcept;
};
}  // namespace std
#endif

// Copies at most cap - 1 bytes; the caller (figram_ffi.c) drops a cut UTF-8 tail.
static void copy_message(char* dst, unsigned long cap, const char* src) {
  if (dst == nullptr || cap == 0) return;
  unsigned long i = 0;
  if (src != nullptr)
    for (; src[i] != 0 && i + 1 < cap; i++) dst[i] = src[i];
  dst[i] = 0;
}

// 0 — fn returned; 1 — fn threw a C++ exception, its text is in err (up to cap bytes with the NUL).
extern "C" __attribute__((visibility("hidden")))
int figram_guard_call(void (*fn)(void*), void* arg, char* err, unsigned long cap) {
  try {
    fn(arg);
    return 0;
  } catch (const std::exception& e) {
    copy_message(err, cap, e.what());
  } catch (...) {
    copy_message(err, cap, "unknown C++ exception");
  }
  return 1;
}
