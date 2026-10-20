// NP-04: figram_init has a plain (non-atomic) "ready" flag: two threads that call it
// at the same time both run lean_initialize_runtime_module + module initialisation.
// Each process run starts N threads on a barrier, all call figram_init, then each
// loads the document and makes one request. Exit 0 = no visible fault this run.
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int figram_init(void);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
void figram_free(char*);
static pthread_barrier_t bar;
static char* doc;
static int results[64];
static void* run(void* p) {
  long i = (long)p;
  pthread_barrier_wait(&bar);
  int r = figram_init();
  void* m = figram_load_doc(doc);
  if (r != 0 || !m) { results[i] = 2; return NULL; }
  char* out = figram_request_json(m, "{\"id\":1,\"op\":\"start\"}");
  results[i] = strstr(out, "\"at\":\"menu\"") ? 0 : 3;
  figram_free(out);
  return NULL;
}
int main(int argc, char** argv) {
  int n = argc > 2 ? atoi(argv[2]) : 8;
  FILE* f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long sz = ftell(f); rewind(f);
  doc = malloc(sz + 1); fread(doc, 1, sz, f); doc[sz] = 0; fclose(f);
  if (argc > 3 && strcmp(argv[3], "serial") == 0) figram_init(); /* control: init once before threads */
  pthread_barrier_init(&bar, NULL, n);
  pthread_t t[64];
  for (long i = 0; i < n; i++) pthread_create(&t[i], NULL, run, (void*)i);
  for (int i = 0; i < n; i++) pthread_join(t[i], NULL);
  int bad = 0;
  for (int i = 0; i < n; i++) if (results[i]) bad++;
  printf("threads=%d bad=%d\n", n, bad);
  return bad ? 1 : 0;
}
