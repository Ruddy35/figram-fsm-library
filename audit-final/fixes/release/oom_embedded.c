/* Нехватка памяти во встроенном режиме: библиотека в процессе хоста. argv: документ, МБ события, предел запроса */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "figram.h"
int main(int argc, char** argv) {
  FILE* f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
  char* doc = malloc(n + 1); fread(doc, 1, n, f); doc[n] = 0; fclose(f);
  size_t mb = strtoul(argv[2], 0, 10), lim = strtoul(argv[3], 0, 10);
  int rc = figram_init();
  printf("хост pid %d: figram_init = %d %s\n", getpid(), rc, rc ? figram_last_error() : ""); fflush(stdout);
  if (rc) return 2;
  void* m = figram_load_doc(doc);
  char* r = figram_request_json(m, "{\"id\":1,\"op\":\"start\"}");
  printf("1. до сбоя: %s\n", r); fflush(stdout); figram_free(r);
  figram_set_max_request(lim);
  const char* snap = "{\"id\":2,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\",\"seq\":1},\"event\":\"";
  size_t big = mb << 20, k = strlen(snap);
  char* req = malloc(k + big + 3); memcpy(req, snap, k); memset(req + k, 'x', big); memcpy(req + k + big, "\"}", 3);
  printf("2. запрос %zu МБ, предел запроса %zu байт...\n", mb, lim); fflush(stdout);
  r = figram_request_json(m, req);
  printf("   ответ: %.160s\n", r); fflush(stdout); figram_free(r); free(req);
  r = figram_request_json(m, "{\"id\":3,\"op\":\"start\"}");
  printf("3. хост жив, следующий запрос: %s\n", r); figram_free(r);
  return 0;
}
