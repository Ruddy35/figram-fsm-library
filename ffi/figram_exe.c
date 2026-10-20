// Исполняемые файлы figram и figram-engine: системные размеры рантайма Lean (ffi/figram_rt.c)
// выставляются до main, то есть до запуска рантайма и его потока libuv.
void figram_rt_defaults(void);

__attribute__((constructor)) static void figram_exe_rt_defaults(void) {
  figram_rt_defaults();
}
