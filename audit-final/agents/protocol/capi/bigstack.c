/* A request near the 1 MiB limit with a large `pending` (or `timers`) from a thread with a
   small stack (embedding.md recommends at least 128 KiB). usage: bigstack DOC KB KIND N */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "figram.h"
static char* doc; static char* req; static char* out;
static void* run(void* u) { (void)u; void* m = figram_load_doc(doc); if (!m) { puts("load failed"); return NULL; }
  out = figram_request_json(m, req); figram_unload(m); return NULL; }
int main(int argc, char** argv) {
  FILE* f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
  doc = malloc(n + 1); fread(doc, 1, n, f); doc[n] = 0; fclose(f);
  int kb = atoi(argv[2]); const char* kind = argv[3]; int cnt = atoi(argv[4]);
  req = malloc(64 + 40 * (size_t)cnt + 256); char* p = req;
  if (!strcmp(kind, "pending")) {
    p += sprintf(p, "{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"home\",\"pending\":[");
    for (int i = 0; i < cnt; i++) p += sprintf(p, "%s{\"call\":\"ping\",\"token\":%d}", i ? "," : "", 10 + i);
    p += sprintf(p, "],\"seq\":%d},\"event\":\"call:ping:pong\",\"token\":%d}", cnt + 10, 10 + cnt - 1);
  } else {
    p += sprintf(p, "{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"home\",\"timers\":{");
    for (int i = 0; i < cnt; i++) p += sprintf(p, "%s\"t%d\":1", i ? "," : "", i);
    p += sprintf(p, "},\"seq\":2},\"event\":\"btn:go\",\"guards\":{\"is_vip\":true}}");
  }
  printf("request %zu bytes, %d %s entries, thread stack %d KiB\n", strlen(req), cnt, kind, kb);
  if (figram_init()) { puts("init failed"); return 1; }
  pthread_attr_t at; pthread_attr_init(&at); pthread_attr_setstacksize(&at, (size_t)kb * 1024);
  pthread_t t; pthread_create(&t, &at, run, NULL); pthread_join(t, NULL);
  if (!out) { puts("no reply"); return 1; }
  printf("reply %zu bytes: %.120s\n", strlen(out), out);
  return strstr(out, "\"snapshot\"") ? 0 : 1;
}
