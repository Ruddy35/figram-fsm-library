// C ABI stress test: T threads × N requests to ONE machine, concurrently with L threads that
// load, query and unload OTHER machines (and call check/fmt).
// Every reply is compared with a reference computed beforehand in one thread.
//   ./stress DOC T N L        — the check (also for ASan/UBSan/TSan/valgrind)
//   ./stress bench DOC T N shared|separate — throughput
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdatomic.h>

int figram_init(void);
char* figram_check_json(const char*);
char* figram_fmt_json(const char*);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
void figram_unload(void*);
void figram_free(char*);

static const char* REQS[] = {
  "{\"id\":1,\"op\":\"hello\"}",
  "{\"id\":2,\"op\":\"start\"}",
  "{\"id\":3,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"btn:order\"}",
  "{\"id\":4,\"op\":\"step\",\"snapshot\":{\"at\":\"cart\"},\"event\":\"btn:checkout\"}",
  "{\"id\":5,\"op\":\"step\",\"snapshot\":{\"at\":\"cart\"},\"event\":\"btn:checkout\",\"guards\":{\"cart_not_empty\":true}}",
  "{\"id\":6,\"op\":\"restart\",\"snapshot\":{\"at\":\"cart\",\"seq\":5}}",
  "{\"id\":7,\"op\":\"step\",\"snapshot\":{\"at\":\"gone\"},\"event\":\"x\"}",
  "{\"id\":8,\"op\":\"nope\"}",
  "[1,2,3]",
  "{\"id\":9",
  /* protocol version 2: start with a first event, seq and guards; the snapshot field back
     (a "back" arrow, an ignored stale timer that keeps back, recovery with an event that keeps
     back, restart that clears it); a timer event cannot start a user */
  "{\"id\":10,\"op\":\"start\",\"event\":\"btn:order\",\"seq\":41}",
  "{\"id\":11,\"op\":\"start\",\"event\":\"cmd:help\",\"guards\":{\"cart_not_empty\":true}}",
  "{\"id\":12,\"op\":\"step\",\"snapshot\":{\"at\":\"help\",\"back\":\"cart\",\"resume\":{\"order\":\"cart\"},\"timers\":{\"cart_reminder\":3},\"seq\":4},\"event\":\"btn:back\"}",
  "{\"id\":13,\"op\":\"step\",\"snapshot\":{\"at\":\"gone\",\"back\":\"cart\",\"timers\":{\"cart_reminder\":3},\"seq\":4},\"event\":\"btn:order\"}",
  "{\"id\":14,\"op\":\"restart\",\"snapshot\":{\"at\":\"catalog\",\"back\":\"menu\",\"resume\":{\"order\":\"catalog\"},\"seq\":4}}",
  "{\"id\":15,\"op\":\"start\",\"event\":\"timer:cart_reminder\"}",
  "{\"id\":16,\"op\":\"step\",\"snapshot\":{\"at\":\"cart\",\"back\":\"menu\",\"resume\":{\"order\":\"cart\"},\"timers\":{\"cart_reminder\":3},\"seq\":3},\"event\":\"timer:cart_reminder\",\"token\":2}",
};
#define NREQ (sizeof REQS / sizeof *REQS)
/* The replies of figram-engine examples/pizza.bot.json to the protocol-2 requests above (from
   REQS[V2_FIRST] on): the shared handle must give exactly the bytes of the process. */
#define V2_FIRST 10
static const char* V2_WANT[] = {
  "{\"id\":10,\"snapshot\":{\"at\":\"catalog\",\"resume\":{\"order\":\"catalog\"},\"seq\":42},\"actions\":[{\"send\":\"catalog\"}]}",
  "{\"id\":11,\"snapshot\":{\"at\":\"help\",\"seq\":1},\"actions\":[{\"send\":\"help\"}]}",
  "{\"id\":12,\"snapshot\":{\"at\":\"cart\",\"back\":\"help\",\"resume\":{\"order\":\"cart\"},\"timers\":{\"cart_reminder\":5},\"seq\":5},\"actions\":[{\"cancel_timer\":\"cart_reminder\"},{\"start_timer\":\"cart_reminder\",\"ms\":7200000,\"token\":5},{\"send\":\"cart\"}]}",
  "{\"id\":13,\"snapshot\":{\"at\":\"catalog\",\"back\":\"cart\",\"resume\":{\"order\":\"catalog\"},\"seq\":5},\"actions\":[{\"cancel_timer\":\"cart_reminder\"},{\"send\":\"catalog\"}],\"recovered\":true}",
  "{\"id\":14,\"snapshot\":{\"at\":\"menu\",\"seq\":5},\"actions\":[{\"send\":\"menu\"}]}",
  "{\"id\":15,\"error\":\"a timer event cannot start a user\",\"code\":\"bad_request\"}",
  "{\"id\":16,\"snapshot\":{\"at\":\"cart\",\"back\":\"menu\",\"resume\":{\"order\":\"cart\"},\"timers\":{\"cart_reminder\":3},\"seq\":3},\"actions\":[]}",
};
#define NV2 (sizeof V2_WANT / sizeof *V2_WANT)
static char* want[NREQ];
static char* doc;
static char* want_check;
static char* want_fmt;
static void* bot;
static int N;
static atomic_int stop;
static atomic_long other_cycles;

static char* read_all(const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) { perror(path); exit(2); }
  fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
  char* b = malloc(n + 1);
  if (fread(b, 1, n, f) != (size_t)n) exit(2);
  b[n] = 0; fclose(f); return b;
}

static void* user(void* arg) {
  long bad = 0, seed = (long)arg;
  for (int i = 0; i < N; i++) {
    unsigned k = (unsigned)(seed * 7 + i) % NREQ;
    char* r = figram_request_json(bot, REQS[k]);
    if (strcmp(r, want[k])) { if (!bad) fprintf(stderr, "MISMATCH req %u: %s\n", k, r); bad++; }
    figram_free(r);
  }
  return (void*)bad;
}

static void* other(void* arg) {
  long bad = 0;
  (void)arg;
  while (!atomic_load(&stop)) {
    void* m = figram_load_doc(doc);
    if (!m) { bad++; continue; }
    for (unsigned k = 0; k < NREQ; k++) {
      char* r = figram_request_json(m, REQS[k]);
      if (strcmp(r, want[k])) bad++;
      figram_free(r);
    }
    figram_unload(m);
    char* c = figram_check_json(doc); if (strcmp(c, want_check)) bad++; figram_free(c);
    char* f = figram_fmt_json(doc); if (strcmp(f, want_fmt)) bad++; figram_free(f);
    atomic_fetch_add(&other_cycles, 1);
  }
  return (void*)bad;
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static void* bench_worker(void* arg) {
  void* m = arg ? arg : bot;
  for (int i = 0; i < N; i++) figram_free(figram_request_json(m, REQS[2]));
  return NULL;
}

static int bench(const char* path, int T, int n, int separate) {
  doc = read_all(path); N = n;
  bot = figram_load_doc(doc);
  void* own[256];
  for (int i = 0; i < T; i++) own[i] = separate ? figram_load_doc(doc) : NULL;
  pthread_t th[256];
  double t0 = now();
  for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, bench_worker, own[i]);
  for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
  double el = now() - t0;
  printf("bench threads=%d mode=%s requests=%ld time=%.3fs rate=%.0f req/s\n", T,
         separate ? "separate" : "shared", (long)T * n, el, T * (double)n / el);
  return 0;
}

int main(int argc, char** argv) {
  setvbuf(stdout, NULL, _IONBF, 0);
  if (figram_init()) return 1;
  if (argc == 6 && !strcmp(argv[1], "bench"))
    return bench(argv[2], atoi(argv[3]), atoi(argv[4]), !strcmp(argv[5], "separate"));
  if (argc != 5) { fprintf(stderr, "usage: stress DOC T N L | bench DOC T N shared|separate\n"); return 2; }
  doc = read_all(argv[1]);
  int T = atoi(argv[2]), L = atoi(argv[4]);
  N = atoi(argv[3]);
  if (T < 1 || T > 256 || N < 1 || L < 0 || L > 64) return 2;
  bot = figram_load_doc(doc);
  if (!bot) { printf("doc not loaded\n"); return 1; }
  for (unsigned k = 0; k < NREQ; k++) want[k] = figram_request_json(bot, REQS[k]);
  /* Protocol 2 through the C ABI: the reference replies equal those of the process. */
  long v2bad = 0;
  if (strstr(argv[1], "pizza.bot.json")) {
    for (unsigned k = 0; k < NV2; k++)
      if (strcmp(want[V2_FIRST + k], V2_WANT[k])) {
        fprintf(stderr, "V2 MISMATCH req %u: %s\n  expected %s\n", V2_FIRST + k, want[V2_FIRST + k], V2_WANT[k]);
        v2bad++;
      }
    printf("protocol 2 replies checked against figram-engine: %u, mismatches %ld\n", (unsigned)NV2, v2bad);
  }
  /* Sensitivity self-check: a corrupted reference must produce mismatches. */
  if (getenv("FIGRAM_STRESS_SELFTEST")) want[2][2] = 'X';
  want_check = figram_check_json(doc);
  want_fmt = figram_fmt_json(doc);
  pthread_t th[256], ot[64];
  for (int i = 0; i < L; i++) pthread_create(&ot[i], NULL, other, NULL);
  for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, user, (void*)(long)i);
  long bad = 0, obad = 0;
  for (int i = 0; i < T; i++) { void* b; pthread_join(th[i], &b); bad += (long)b; }
  atomic_store(&stop, 1);
  for (int i = 0; i < L; i++) { void* b; pthread_join(ot[i], &b); obad += (long)b; }
  long total = (long)T * N;
  printf("shared handle: %d threads x %d = %ld requests, mismatches %ld; "
         "%d loader threads: %ld load/10 req/unload/check/fmt cycles, mismatches %ld\n",
         T, N, total, bad, L, atomic_load(&other_cycles), obad);
  for (unsigned k = 0; k < NREQ; k++) figram_free(want[k]);
  figram_free(want_check); figram_free(want_fmt);
  figram_unload(bot);
  if (total == 0 || (L > 0 && atomic_load(&other_cycles) == 0)) { printf("NOTHING CHECKED\n"); return 1; }
  return bad || obad || v2bad;
}
