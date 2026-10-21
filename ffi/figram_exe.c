// Executables figram and figram-engine: the system sizes of the Lean runtime (ffi/figram_rt.c) are
// set before main, that is, before the runtime starts. Also, main runs on the main thread of the
// process (LEAN_MAIN_USE_THREAD=0) rather than in a separate runtime thread: the runtime starts no
// service threads (ffi/figram_init.cpp), so the process is single-threaded; a value set by the user
// takes precedence.
void figram_rt_defaults(void);
int setenv(const char* name, const char* value, int overwrite);

__attribute__((constructor)) static void figram_exe_rt_defaults(void) {
  figram_rt_defaults();
  setenv("LEAN_MAIN_USE_THREAD", "0", 0);
}
