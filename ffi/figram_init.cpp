// Запуск рантайма Lean без цикла событий libuv (P-22). Это определение lean_initialize_runtime_module
// заменяет определение из libleanrt.a (src/runtime/init_module.cpp): объект из архива компоновщик берёт,
// только если символ ещё не определён. Шаги — те же, что в Lean 4.35, кроме:
//  * initialize_libuv — таймеры, сокеты, сигналы и DNS libuv и постоянный поток их цикла событий
//    (с потоком ядра io_uring SQPOLL, ≈8 файловыми дескрипторами и 64 МБ резерва арены glibc malloc).
//    Пользуются ими только функции Std.Internal.UV и Std.Net, которых в программах Figram нет;
//    verify.sh доказывает это по готовым файлам (ci/reach.py: цикл событий недостижим из main
//    и конструкторов);
//  * initialize_stack_overflow — только во встроенной библиотеке (без FIGRAM_STACK_GUARD): обработчик
//    SIGSEGV/SIGBUS, сообщающий о переполнении стека, ставится на весь процесс, а сигналами хоста
//    распоряжается хост. Исполняемые файлы figram и figram-engine его сохраняют.
// verify.sh сверяет последовательность вызовов с определением из libleanrt.a установленного Lean:
// новый шаг инициализации в другой версии Lean остановит проверку, а не пропустится молча.
#include <lean/version.h>
#if LEAN_VERSION_MAJOR != 4 || LEAN_VERSION_MINOR != 35
#error "ffi/figram_init.cpp повторяет src/runtime/init_module.cpp Lean 4.35: сверьте шаги с этой версией Lean"
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

// Идемпотентна, как и в Lean: её вызывают и сгенерированный main, и figram_init.
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
