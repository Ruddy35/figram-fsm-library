// Аудит: отказ malloc в dup() (figram_ffi.c). Собирается с -Wl,--wrap=malloc:
// вызовы malloc из всех объектов (и libfigram.a) идут через __wrap_malloc,
// который один раз отказывает на запросе ровно заданного размера.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int figram_init(void);
char* figram_check_json(const char*);
void figram_free(char*);

void* __real_malloc(size_t);
static size_t fail_size = 0;
static int failed = 0;
void* __wrap_malloc(size_t n) {
  if (fail_size && n == fail_size) { fail_size = 0; failed = 1; return NULL; }
  return __real_malloc(n);
}

int main(void) {
  setvbuf(stdout, NULL, _IONBF, 0);
  if (figram_init()) return 1;
  char* r = figram_check_json("{}");
  size_t n = strlen(r);
  printf("baseline: check({}) → %zu bytes: %s\n", n, r);
  figram_free(r);
  fail_size = n + 1;  /* ровно размер буфера dup() */
  r = figram_check_json("{}");
  printf("with malloc(%zu) failing (injected=%d): returned %p\n", n + 1, failed, (void*)r);
  if (r == NULL) { printf("controlled: NULL returned\n"); return 0; }
  printf("result: %s\n", r);
  figram_free(r);
  return failed ? 3 : 0;
}
