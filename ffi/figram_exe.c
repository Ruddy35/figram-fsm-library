// Исполняемые файлы figram и figram-engine: системные размеры рантайма Lean (ffi/figram_rt.c)
// выставляются до main, то есть до запуска рантайма. Кроме того, main работает на основном потоке
// процесса (LEAN_MAIN_USE_THREAD=0), а не в отдельном потоке рантайма: служебных потоков рантайм не
// запускает (ffi/figram_init.cpp), так что процесс однопоточный; заданное пользователем значение главнее.
void figram_rt_defaults(void);
int setenv(const char* name, const char* value, int overwrite);

__attribute__((constructor)) static void figram_exe_rt_defaults(void) {
  figram_rt_defaults();
  setenv("LEAN_MAIN_USE_THREAD", "0", 0);
}
