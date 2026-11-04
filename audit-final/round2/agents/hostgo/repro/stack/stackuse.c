/* Stack high-water mark of one figram_request_json call as a function of the number of waits in
   `pending` (points.bot.json, outcome event). The request runs on a pthread whose stack (64 MiB,
   mmap) is filled with a pattern; the untouched part is measured after the call.
   Usage: stackuse BOT.json N... */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "figram.h"

static void* bot;
static char* line;
static char* reply;
static void* run(void* a) { (void)a; reply = figram_request_json(bot, line); return NULL; }

static char* mkline(long n) {
  size_t cap = 200 + (size_t)n * 48;
  char* s = malloc(cap);
  long seq = 5000000;
  size_t k = (size_t)sprintf(s, "{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"home\",\"pending\":[");
  for (long i = 0; i < n; i++) k += (size_t)sprintf(s + k, "%s{\"call\":\"award\",\"token\":%ld}", i ? "," : "", seq - i);
  k += (size_t)sprintf(s + k, "],\"seq\":%ld},\"event\":\"call:award:ok\",\"token\":%ld}", seq, n ? seq - n + 1 : seq);
  return s;
}

int main(int argc, char** argv) {
  FILE* f = fopen(argv[1], "rb");
  static char doc[1 << 20];
  size_t dn = fread(doc, 1, sizeof doc - 1, f);
  doc[dn] = 0;
  fclose(f);
  figram_set_max_request(0);
  char* err = NULL;
  bot = figram_load_doc_ex(doc, &err);
  if (!bot) { fprintf(stderr, "load: %s\n", err); return 1; }
  size_t ss = (size_t)64 << 20;
  for (int a = 2; a < argc; a++) {
    long n = atol(argv[a]);
    line = mkline(n);
    unsigned char* st = mmap(NULL, ss, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    memset(st, 0xA5, ss);
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstack(&at, st, ss);
    pthread_t t;
    pthread_create(&t, &at, run, NULL);
    pthread_join(t, NULL);
    size_t untouched = 0;
    while (untouched < ss && st[untouched] == 0xA5) untouched++;
    printf("waits=%ld request=%zu bytes stack_used=%zu KiB reply=%.60s\n", n, strlen(line), (ss - untouched) >> 10, reply);
    figram_free(reply);
    munmap(st, ss);
    free(line);
  }
  figram_unload(bot);
  return 0;
}
