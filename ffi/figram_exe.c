// Исполняемые файлы figram и figram-engine: системные размеры рантайма Lean (ffi/figram_rt.c)
// выставляются до main, то есть до запуска рантайма и его потока libuv. Кроме того — только для
// исполняемых файлов (во встроенном режиме это решения хоста):
//  * main работает на основном потоке процесса (LEAN_MAIN_USE_THREAD=0), а не в отдельном
//    потоке рантайма: одним потоком и одним стеком меньше; заданное пользователем значение главнее;
//  * потоки делят одну арену glibc malloc (M_ARENA_MAX = 1, если не задан MALLOC_ARENA_MAX): иначе
//    glibc резервирует по 64 МБ адресов на каждый поток, вызвавший malloc. Объекты Lean выделяет
//    mimalloc, glibc malloc нужен только служебному коду, так что общая арена ничего не замедляет.
void figram_rt_defaults(void);
char* getenv(const char* name);
int setenv(const char* name, const char* value, int overwrite);
int mallopt(int param, int value);
#define FG_M_ARENA_MAX (-8)   /* M_ARENA_MAX из <malloc.h> glibc */

__attribute__((constructor)) static void figram_exe_rt_defaults(void) {
  figram_rt_defaults();
  setenv("LEAN_MAIN_USE_THREAD", "0", 0);
#if defined(__linux__)
  if (!getenv("MALLOC_ARENA_MAX")) mallopt(FG_M_ARENA_MAX, 1);
#endif
}
