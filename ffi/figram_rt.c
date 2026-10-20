// Системные размеры для рантайма Lean (P-09). По умолчанию рантайм резервирует под стек каждого
// своего потока 1 ГиБ адресов, а распределитель mimalloc — арену 1 ГиБ; реально занято ≈10 МБ,
// но под `ulimit -v` (RLIMIT_AS) такой резерв не помещается. Здесь стек потоков рантайма
// становится таким же, как у любого потока процесса (RLIMIT_STACK, `ulimit -s`), а mimalloc не
// резервирует арену заранее (arena_reserve = 0: память берётся у ОС по мере надобности; любой
// ненулевой резерв mimalloc округляет до 64 МиБ, и при таком же запасе адресов рантайм падал бы
// с «INTERNAL PANIC: out of memory»). Явно заданные LEAN_STACK_SIZE_KB и MIMALLOC_ARENA_RESERVE главнее.
// Вызывать до lean_initialize_runtime_module(): figram_init (ffi/figram_ffi.c) и конструктор
// исполняемых файлов (ffi/figram_exe.c).
#include <lean/mimalloc.h>

/* Объявления из libc и рантайма: leanc официального релиза Lean компилирует с -nostdinc. */
char* getenv(const char* name);
int getrlimit(int resource, void* rlim);
void* lean_internal_set_thread_stack_size(size_t sz);
#define FG_RLIMIT_STACK 3

#define FG_DEFAULT_STACK (8ul << 20)    /* как у glibc, если RLIMIT_STACK не задан */
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
