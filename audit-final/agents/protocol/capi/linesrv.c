/* figram-engine over the C ABI: one request per stdin line -> figram_request_json_n -> stdout. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "figram.h"
int main(int argc, char** argv) {
  FILE* f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
  char* doc = malloc(n + 1); fread(doc, 1, n, f); doc[n] = 0; fclose(f);
  void* m = figram_load_doc(doc); if (!m) { fprintf(stderr, "load: %s\n", figram_last_error()); return 1; }
  char* line = NULL; size_t cap = 0; ssize_t len;
  while ((len = getline(&line, &cap, stdin)) > 0) {
    if (line[len - 1] == '\n') len--;
    char* r = figram_request_json_n(m, line, (size_t)len);
    fputs(r, stdout); fputc('\n', stdout); figram_free(r);
  }
  figram_unload(m); return 0;
}
