// C++-часть libfigram.a (P-09): вызов функции C с перехватом исключений C++.
// Рантайм Lean сообщает о части отказов исключением C++ (lean::exception, std::bad_alloc).
// Через C-кадры figram_init исключение дошло бы до хоста, и std::terminate завершил бы
// весь процесс (SIGABRT). figram_guard_call ловит его и возвращает текст. Раньше так приходило
// «failed to create thread» (стек потока libuv под RLIMIT_AS); с ffi/figram_init.cpp запуск
// рантайма потоков не создаёт, и известных причин исключения при запуске в Lean 4.35 нет.
//
// Без системных заголовков: leanc официального релиза Lean компилирует с -nostdinc
// (доступны только встроенные заголовки clang), поэтому при отсутствии <exception>
// std::exception объявлен здесь. Объявление совпадает по ABI Itanium с libstdc++ и libc++
// (класс в пространстве std без версии, виртуальные ~exception и what).
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

// Копия не длиннее cap - 1 байт; обрезанный хвост UTF-8 убирает вызывающий (figram_ffi.c).
static void copy_message(char* dst, unsigned long cap, const char* src) {
  if (dst == nullptr || cap == 0) return;
  unsigned long i = 0;
  if (src != nullptr)
    for (; src[i] != 0 && i + 1 < cap; i++) dst[i] = src[i];
  dst[i] = 0;
}

// 0 — fn завершилась; 1 — fn выбросила исключение C++, его текст записан в err (до cap байт с NUL).
extern "C" __attribute__((visibility("hidden")))
int figram_guard_call(void (*fn)(void*), void* arg, char* err, unsigned long cap) {
  try {
    fn(arg);
    return 0;
  } catch (const std::exception& e) {
    copy_message(err, cap, e.what());
  } catch (...) {
    copy_message(err, cap, "неизвестное исключение C++");
  }
  return 1;
}
