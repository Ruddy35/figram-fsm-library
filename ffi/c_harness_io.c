#include <stdio.h>
#include <stdlib.h>
int read_file(const char* path, char** out) {
  FILE* f = fopen(path, "rb"); if (!f) return 1;
  fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
  *out = malloc(n + 1); size_t got = fread(*out, 1, n, f); (*out)[got] = 0; fclose(f); return got == (size_t)n ? 0 : 1;
}
