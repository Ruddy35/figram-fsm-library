// Аудит C-ABI figram_ffi.c: граничные случаи контракта.
// Каждый подтест — отдельный запуск процесса: ./contract <подтест> [аргументы].
// Аварийные подтесты (NULL, вызов до init, двойная выгрузка) ожидаемо падают;
// их результат (код выхода / сигнал) фиксирует вызывающий скрипт.
#define _GNU_SOURCE
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>

int figram_init(void);
char* figram_check_json(const char*);
char* figram_fmt_json(const char*);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
void figram_unload(void*);
void figram_free(char*);

static const char* REQ =
    "{\"id\":7,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"btn:order\"}";
static const char* HELLO = "{\"id\":1,\"op\":\"hello\"}";

static char* read_all(const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) { perror(path); exit(2); }
  fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
  char* b = malloc(n + 1);
  if (fread(b, 1, n, f) != (size_t)n) exit(2);
  b[n] = 0; fclose(f); return b;
}

static long status_kb(const char* key) {
  FILE* f = fopen("/proc/self/status", "r");
  char line[256]; long v = -1; size_t kl = strlen(key);
  while (f && fgets(line, sizeof line, f))
    if (!strncmp(line, key, kl) && line[kl] == ':') { v = atol(line + kl + 1); break; }
  if (f) fclose(f);
  return v;
}

static int n_threads(void) {
  DIR* d = opendir("/proc/self/task"); int n = 0; struct dirent* e;
  while (d && (e = readdir(d))) if (e->d_name[0] != '.') n++;
  if (d) closedir(d);
  return n;
}

/* ---------- init ---------- */

static int t_init_twice(void) {
  int a = figram_init(), b = figram_init();
  char* r = figram_check_json("{}");
  printf("init1=%d init2=%d check={}→%s\n", a, b, r);
  figram_free(r);
  return a || b;
}

static pthread_barrier_t bar;
static const char* g_doc;
static void* init_worker(void* arg) {
  pthread_barrier_wait(&bar);
  long rc = figram_init();
  void* m = figram_load_doc(g_doc);
  char* r = m ? figram_request_json(m, HELLO) : NULL;
  long ok = rc == 0 && m && r && strstr(r, "\"protocol\":1");
  if (r) figram_free(r);
  if (m) figram_unload(m);
  return (void*)ok;
}
/* Два потока одновременно вызывают figram_init и сразу работают с автоматом. */
static int t_init_race(const char* path) {
  g_doc = read_all(path);
  pthread_t a, b;
  pthread_barrier_init(&bar, NULL, 2);
  pthread_create(&a, NULL, init_worker, NULL);
  pthread_create(&b, NULL, init_worker, NULL);
  void *ra, *rb;
  pthread_join(a, &ra); pthread_join(b, &rb);
  printf("init_race: thread A ok=%ld, thread B ok=%ld\n", (long)ra, (long)rb);
  return !(ra && rb);
}

/* ---------- до init ---------- */

static int t_before_init(const char* what, const char* path) {
  fprintf(stderr, "calling %s before figram_init\n", what);
  if (!strcmp(what, "check")) { char* r = figram_check_json("{}"); printf("check → %s\n", r); }
  else if (!strcmp(what, "fmt")) { char* r = figram_fmt_json("{}"); printf("fmt → %s\n", r); }
  else if (!strcmp(what, "load")) {
    void* m = figram_load_doc(read_all(path));
    printf("load → %p\n", m);
    if (m) { char* r = figram_request_json(m, HELLO); printf("request → %s\n", r); }
  }
  return 0;
}

/* ---------- NULL ---------- */

static int t_null(const char* what, const char* path) {
  figram_init();
  void* m = figram_load_doc(read_all(path));
  fprintf(stderr, "NULL case %s\n", what);
  if (!strcmp(what, "check")) figram_check_json(NULL);
  else if (!strcmp(what, "fmt")) figram_fmt_json(NULL);
  else if (!strcmp(what, "load")) figram_load_doc(NULL);
  else if (!strcmp(what, "line")) figram_request_json(m, NULL);
  else if (!strcmp(what, "handle")) figram_request_json(NULL, HELLO);
  else if (!strcmp(what, "unload")) figram_unload(NULL);
  else if (!strcmp(what, "free")) figram_free(NULL);
  /* Типичный код C: загрузка не удалась, выгрузка без проверки. */
  else if (!strcmp(what, "unload_failed_load")) figram_unload(figram_load_doc("{\"figram\":1}"));
  printf("NULL case %s: returned normally\n", what);
  return 0;
}

/* ---------- время жизни ---------- */

/* Двойная выгрузка. Затем продолжаем работать, чтобы порча кучи проявилась. */
static int t_unload_twice(const char* path) {
  figram_init();
  char* doc = read_all(path);
  void* m = figram_load_doc(doc);
  char* want = figram_request_json(m, REQ);
  figram_unload(m);
  figram_unload(m);
  printf("double unload: returned normally\n");
  long bad = 0;
  for (int i = 0; i < 2000; i++) {
    void* k = figram_load_doc(doc);
    char* r = figram_request_json(k, REQ);
    if (strcmp(r, want)) bad++;
    figram_free(r); figram_unload(k);
  }
  printf("after double unload: 2000 load/request/unload, mismatches %ld\n", bad);
  return 0;
}

/* Запрос к выгруженному автомату: память переиспользована другим документом. */
static int t_use_after_unload(const char* path, const char* other) {
  figram_init();
  void* m = figram_load_doc(read_all(path));
  char* before = figram_request_json(m, HELLO);
  figram_unload(m);
  void* k = other ? figram_load_doc(read_all(other)) : NULL;
  char* after = figram_request_json(m, HELLO);  /* UAF */
  printf("use after unload: before=%s\n                  after =%s\n", before, after);
  printf("use after unload: %s\n", strcmp(before, after) ? "ANSWER CHANGED" : "same answer");
  (void)k;
  return 0;
}

/* ---------- потоки ---------- */

static int leak_ctl = 0;  /* контроль чувствительности: каждый поток теряет 64 КБ */
void* volatile leak_sink;
static void* one_call(void* arg) {
  void* m = arg;
  if (leak_ctl) { char* p = malloc(64 << 10); memset(p, 1, 64 << 10); leak_sink = p; }
  char* r = figram_request_json(m, REQ);
  long ok = strstr(r, "\"snapshot\"") != NULL;
  figram_free(r);
  return (void*)ok;
}
/* N короткоживущих потоков, каждый делает один вызов: рост RSS/VmSize. */
static int t_threads(const char* path, int n) {
  figram_init();
  leak_ctl = getenv("FIGRAM_THREADS_LEAK") != NULL;
  void* m = figram_load_doc(read_all(path));
  for (int i = 0; i < 50; i++) { pthread_t t; pthread_create(&t, NULL, one_call, m); pthread_join(t, NULL); }
  long rss0 = status_kb("VmRSS"), vm0 = status_kb("VmSize");
  long ok = 0;
  for (int i = 0; i < n; i++) {
    pthread_t t; void* r;
    if (pthread_create(&t, NULL, one_call, m)) { printf("pthread_create failed at %d\n", i); return 1; }
    pthread_join(t, &r); ok += (long)r;
  }
  long rss1 = status_kb("VmRSS"), vm1 = status_kb("VmSize");
  printf("threads=%d ok=%ld RSS %ld→%ld KB (Δ%ld), VmSize %ld→%ld KB (Δ%ld), live threads %d\n",
         n, ok, rss0, rss1, rss1 - rss0, vm0, vm1, vm1 - vm0, n_threads());
  figram_unload(m);
  return ok != n;
}

/* ---------- побочные эффекты figram_init для процесса-хоста ---------- */

static void report_env(const char* when) {
  struct sigaction sp; sigaction(SIGPIPE, NULL, &sp);
  struct sigaction ss; sigaction(SIGSEGV, NULL, &ss);
  stack_t st; sigaltstack(NULL, &st);
  printf("%-11s threads=%d VmSize=%ld KB SIGPIPE=%s SIGSEGV=%s sigaltstack=%s size=%zu\n", when,
         n_threads(), status_kb("VmSize"),
         sp.sa_handler == SIG_IGN ? "SIG_IGN" : sp.sa_handler == SIG_DFL ? "SIG_DFL" : "handler",
         ss.sa_handler == SIG_DFL ? "SIG_DFL" : "handler",
         (st.ss_flags & SS_DISABLE) ? "disabled" : "enabled", (size_t)st.ss_size);
}
static int t_side_effects(void) {
  report_env("before init");
  if (figram_init()) return 1;
  report_env("after init");
  return 0;
}

/* ---------- входные строки ---------- */

/* Встроенный NUL: lean_mk_string обрезает по strlen. */
static int t_nul(const char* path) {
  figram_init();
  char* doc = read_all(path);
  size_t n = strlen(doc);
  char* glued = malloc(n + 32);
  memcpy(glued, doc, n);
  memcpy(glued + n, "\0garbage{{{", 12);
  /* Для C это та же строка: обрезка неизбежна, но длина не передаётся вовсе. */
  void* m = figram_load_doc(glued);
  printf("load(doc + NUL + garbage) → %s\n", m ? "LOADED (garbage after NUL ignored)" : "rejected");
  char* r = figram_check_json("{\"figram\":1}\0{\"bad\"");
  printf("check(\"{\\\"figram\\\":1}\\0...\") → %s\n", r);
  figram_free(r);
  /* Ответ с NUL: экранированный \u0000 в ключе попадает в текст ошибки сырым байтом. */
  const char* dup_key = "{\"figram\":1,\"a\\u0000b\":1,\"a\\u0000b\":2}";
  r = figram_check_json(dup_key);
  printf("check(dup key \"a\\u0000b\") → strlen=%zu text=%s\n", strlen(r), r);
  size_t k = strlen(r);
  printf("  bytes after first NUL: %s\n", r[k + 1] ? r + k + 1 : "(none)");
  figram_free(r);
  if (m) figram_unload(m);
  return 0;
}

/* Невалидный UTF-8: lean_mk_string заменяет на U+FFFD; сравните с figram-engine. */
static int t_utf8(const char* path) {
  figram_init();
  void* m = figram_load_doc(read_all(path));
  const char* reqs[] = {
    "{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"btn:order\xff\"}",
    "{\"id\":2,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\xc3\"},\"event\":\"btn:order\"}",
    "{\"id\":3,\"op\":\"hello\"}\xff",
    "{\"id\":4,\"op\":\"hel\xedlo\"}",
  };
  for (unsigned i = 0; i < sizeof reqs / sizeof *reqs; i++) {
    char* r = figram_request_json(m, reqs[i]);
    printf("utf8 req %u → %s\n", i + 1, r);
    figram_free(r);
  }
  figram_unload(m);
  return 0;
}

static int usage(void) {
  fprintf(stderr, "usage: contract init_twice | init_race DOC | before_init check|fmt|load DOC |\n"
                  "  null check|fmt|load|line|handle|unload|free|unload_failed_load DOC |\n"
                  "  unload_twice DOC | use_after_unload DOC [OTHER] | threads DOC N |\n"
                  "  side_effects | nul DOC | utf8 DOC\n");
  return 2;
}

int main(int argc, char** argv) {
  setvbuf(stdout, NULL, _IONBF, 0);
  if (argc < 2) return usage();
  const char* t = argv[1];
  if (!strcmp(t, "init_twice")) return t_init_twice();
  if (!strcmp(t, "init_race") && argc > 2) return t_init_race(argv[2]);
  if (!strcmp(t, "before_init") && argc > 3) return t_before_init(argv[2], argv[3]);
  if (!strcmp(t, "null") && argc > 3) return t_null(argv[2], argv[3]);
  if (!strcmp(t, "unload_twice") && argc > 2) return t_unload_twice(argv[2]);
  if (!strcmp(t, "use_after_unload") && argc > 2) return t_use_after_unload(argv[2], argc > 3 ? argv[3] : NULL);
  if (!strcmp(t, "threads") && argc > 3) return t_threads(argv[2], atoi(argv[3]));
  if (!strcmp(t, "side_effects")) return t_side_effects();
  if (!strcmp(t, "nul") && argc > 2) return t_nul(argv[2]);
  if (!strcmp(t, "utf8") && argc > 2) return t_utf8(argv[2]);
  return usage();
}
