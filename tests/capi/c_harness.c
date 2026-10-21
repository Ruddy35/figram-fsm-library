// Check of the C shim: invalid UTF-8, empty and huge inputs, concurrent calls from POSIX
// threads to one immutable machine. Expectations per docs/embedding.md: errors of the shim are
// {"id":null,"error":…,"code":…} (an empty request and invalid UTF-8 — parse_error, longer than
// the 1 MiB limit — too_large).
int printf(const char*, ...);
char* strstr(const char*, const char*);
int strncmp(const char*, const char*, unsigned long);
void* malloc(unsigned long);
void free(void*);
void* memset(void*, int, unsigned long);
int strcmp(const char*, const char*);
typedef unsigned long pthread_t;
int pthread_create(pthread_t*, const void*, void* (*)(void*), void*);
int pthread_join(pthread_t, void**);

int figram_init(void);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
char* figram_check_json(const char*);
void figram_free(char*);
void figram_unload(void*);
int sscanf(const char*, const char*, ...);
typedef struct FILE FILE;
FILE* fopen(const char*, const char*);
char* fgets(char*, int, FILE*);
int fclose(FILE*);

static const char* REQ = "{\"id\":7,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"btn:order\"}";

/* Resident memory of the process in KB (Linux). */
static long rss_kb(void) {
  FILE* f = fopen("/proc/self/statm", "r");
  char buf[128]; long pages = 0, rss = 0;
  if (f && fgets(buf, sizeof buf, f)) sscanf(buf, "%ld %ld", &pages, &rss);
  if (f) fclose(f);
  return rss * 4;
}

/* Loads and unloads the document n times with a request to each; returns the memory growth. */
static long reload_growth(const char* doc, int n, int unload) {
  for (int i = 0; i < 50; i++) {                 /* allocator warm-up */
    void* m = figram_load_doc(doc);
    figram_free(figram_request_json(m, REQ));
    figram_unload(m);
  }
  long before = rss_kb();
  for (int i = 0; i < n; i++) {
    void* m = figram_load_doc(doc);
    char* r = figram_request_json(m, REQ);
    figram_free(r);
    if (unload) figram_unload(m);
  }
  return rss_kb() - before;
}

static void* bot;
static char* expected;

/* The reply is an error (code "" — any; otherwise with this code and id null); code NULL — not an error. */
static int expect_error(const char* r, const char* code) {
  if (!r) return 0;
  int is_err = strstr(r, "\"error\":") != 0;
  if (!code) return !is_err;
  if (!is_err) return 0;
  if (!*code) return 1;
  char want[64]; int k = 0;
  const char* pre = "\"code\":\"";
  for (const char* p = pre; *p; p++) want[k++] = *p;
  for (const char* p = code; *p && k < 60; p++) want[k++] = *p;
  want[k++] = '"'; want[k] = 0;
  return strstr(r, want) != 0 && strncmp(r, "{\"id\":null,", 11) == 0;
}

static void* worker(void* arg) {
  long bad = 0;
  for (int i = 0; i < 20000; i++) {
    char* r = figram_request_json(bot, REQ);
    if (strcmp(r, expected) != 0) bad++;
    figram_free(r);
  }
  return (void*)bad;
}

int main(int argc, char** argv) {
  if (figram_init()) return 1;
  extern int read_file(const char*, char**);
  char* doc; if (read_file(argv[1], &doc)) return 1;
  bot = figram_load_doc(doc);
  if (!bot) { printf("document not loaded\n"); return 1; }
  void* bad_doc = figram_load_doc("{\"figram\": 1}");
  printf("invalid document: %s\n", bad_doc ? "LOADED" : "rejected");

  /* code: the expected error code of the shim; "" — an engine error (the code is not checked);
     NULL — not an error. */
  struct { const char* in; const char* code; } nasty[] = {
    {"", "parse_error"}, {"{", ""}, {"\xff\xfe\xfd", "parse_error"},
    {"{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"\xff\"},\"event\":\"x\"}", "parse_error"},
    {"{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"\xc3\"}", "parse_error"},
    {"{\"id\":\"\xed\xa0\x80\",\"op\":\"hello\"}", "parse_error"}, {"null", ""}, {"[]", ""},
    {"{\"op\":\"start\"}", 0},
  };
  int bad_inputs = 0;
  for (unsigned i = 0; i < sizeof(nasty) / sizeof(nasty[0]); i++) {
    char* r = figram_request_json(bot, nasty[i].in);
    printf("input %u → %.110s\n", i, r);
    if (!expect_error(r, nasty[i].code)) { printf("FAIL: input %u: expected %s\n", i, nasty[i].code ? nasty[i].code : "no error"); bad_inputs++; }
    figram_free(r);
  }
  unsigned long n = 5u << 20;                    // a 5 MB string: longer than the 1 MiB limit
  char* big = malloc(n + 64);
  memset(big, 'a', n + 64);
  big[0] = '"'; big[n] = '"'; big[n + 1] = 0;
  char* r = figram_request_json(bot, big);
  printf("5 MB string → %.80s\n", r);
  if (!expect_error(r, "too_large")) { printf("FAIL: 5 MB string: expected too_large\n"); bad_inputs++; }
  figram_free(r); free(big);

  expected = figram_request_json(bot, REQ);
  pthread_t th[16];
  for (int i = 0; i < 16; i++) pthread_create(&th[i], 0, worker, 0);
  long bad = 0;
  for (int i = 0; i < 16; i++) { void* b; pthread_join(th[i], &b); bad += (long)b; }
  printf("16 threads × 20000 calls to one machine: mismatches %ld\n", bad);
  figram_free(expected);

  /* The memory measurement makes sense only without ASan (it quarantines freed memory). */
  int reload_ok = 1;
  if (argc > 2) {
    long with_unload = reload_growth(doc, 5000, 1);
    long without_unload = reload_growth(doc, 5000, 0);
    printf("5000 loads and unloads: memory growth %ld KB; without unload (control): %ld KB\n",
           with_unload, without_unload);
    reload_ok = with_unload < 2048 && without_unload > 8192;
  }
  if (!reload_ok) printf("FAIL: unload does not free memory, or the control is insensitive\n");
  figram_unload(bot);
  if (bad_inputs) printf("FAIL: %d edge-case inputs got a wrong reply\n", bad_inputs);
  return bad != 0 || !reload_ok || bad_inputs != 0;
}
