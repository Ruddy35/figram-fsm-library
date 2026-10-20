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
#include <sys/resource.h>

int figram_init(void);
char* figram_check_json(const char*);
char* figram_fmt_json(const char*);
void* figram_load_doc(const char*);
char* figram_request_json(void*, const char*);
void figram_unload(void*);
void figram_free(char*);
/* Функции, добавленные исправлениями P-09/P-18/P-21 (fix-ffi). Слабые объявления:
   тест собирается и со старой библиотекой и сообщает «MISSING» вместо ошибки компоновки. */
#define WEAK __attribute__((weak))
WEAK const char* figram_last_error(void);
WEAK void* figram_load_doc_ex(const char*, char**);
WEAK void* figram_load_doc_n(const char*, size_t);
WEAK char* figram_check_json_n(const char*, size_t);
WEAK char* figram_fmt_json_n(const char*, size_t);
WEAK char* figram_request_json_n(void*, const char*, size_t);
WEAK size_t figram_set_max_request(size_t);

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

/* ======== Регрессионные тесты исправлений fix-ffi (P-09, P-18, P-21, P-22) ========
   Каждый подтест печатает строки «ok …» / «FAIL …» и возвращает 0, только если все
   проверки прошли. На старой библиотеке отсутствующие функции дают «FAIL … MISSING». */

static int n_fail = 0;
static void ok_if(int cond, const char* what, const char* detail) {
  printf("%s %s%s%.200s\n", cond ? "ok  " : "FAIL", what, detail ? ": " : "", detail ? detail : "");
  if (!cond) n_fail++;
}
#define HAVE(f) ((f) != NULL ? 1 : (ok_if(0, #f, "MISSING (функции нет в библиотеке)"), 0))

/* Ответ — ошибка протокола с заданным кодом (и id null, если nullid). */
static int is_error_code(const char* r, const char* code, int nullid) {
  if (!r) return 0;
  char want[64];
  snprintf(want, sizeof want, "\"code\":\"%s\"", code);
  return strstr(r, "\"error\":") && strstr(r, want) && (!nullid || !strncmp(r, "{\"id\":null,", 11));
}
static const char* err_or(const char* d) {
  const char* e = figram_last_error ? figram_last_error() : NULL;
  return e ? e : d;
}

/* P-09: figram_init под RLIMIT_AS не роняет процесс, а возвращает код ≠ 0 с причиной. */
static int t_rlimit_init(void) {
  /* FIGRAM_TEST_AS_HEADROOM=N: сам задать RLIMIT_AS = занято сейчас + N байт (как хост, который
     ставит лимит после старта). N = 1073741824 — окно, где арена mimalloc занимает весь запас. */
  const char* h = getenv("FIGRAM_TEST_AS_HEADROOM");
  if (h) {
    long vm = status_kb("VmSize");
    struct rlimit rl; getrlimit(RLIMIT_AS, &rl);
    rl.rlim_cur = (rlim_t)vm * 1024 + strtoull(h, NULL, 10);
    if (setrlimit(RLIMIT_AS, &rl)) { perror("setrlimit"); return 2; }
    printf("RLIMIT_AS set to %llu (VmSize %ld KB + %s)\n", (unsigned long long)rl.rlim_cur, vm, h);
  }
  int rc = figram_init();
  const char* e = figram_last_error ? figram_last_error() : NULL;
  printf("figram_init=%d last_error=%s\n", rc, e ? e : "(none)");
  if (rc == 0) {                                /* лимита нет или он достаточен — тоже норма */
    char* r = figram_check_json("{}"); ok_if(r != NULL, "init ok, check works", r); figram_free(r);
    return n_fail != 0;
  }
  ok_if(e && *e, "init failure has a reason (figram_last_error)", e);
  ok_if(figram_init() == rc, "second figram_init returns the same code", NULL);
  /* Отказ init не ломает остальные функции: они сообщают ошибку, а не падают. */
  void* m = figram_load_doc("{\"figram\":1}");
  ok_if(m == NULL, "load after failed init returns NULL", NULL);
  char* c = figram_check_json("{}");
  ok_if(c != NULL && !strncmp(c, "error:", 6), "check after failed init returns an error text", c);
  if (c) figram_free(c);
  return n_fail != 0;
}

/* P-21: вызовы без figram_init работают (ленивая инициализация). */
static int t_lazy(const char* path) {
  char* doc = read_all(path);
  void* m = figram_load_doc(doc);
  ok_if(m != NULL, "load_doc without figram_init", NULL);
  char* r = m ? figram_request_json(m, HELLO) : NULL;
  ok_if(r && strstr(r, "\"protocol\":1"), "request without figram_init", r);
  if (r) figram_free(r);
  if (m) figram_unload(m);
  return n_fail != 0;
}

/* P-21: ленивая инициализация из нескольких потоков одновременно (без figram_init). */
static void* lazy_worker(void* arg) {
  (void)arg;
  pthread_barrier_wait(&bar);
  void* m = figram_load_doc(g_doc);
  char* r = m ? figram_request_json(m, HELLO) : NULL;
  long ok = m && r && strstr(r, "\"protocol\":1");
  if (r) figram_free(r);
  if (m) figram_unload(m);
  return (void*)ok;
}
static int t_lazy_race(const char* path) {
  g_doc = read_all(path);
  enum { NT = 8 };
  pthread_t th[NT]; void* res[NT];
  pthread_barrier_init(&bar, NULL, NT);
  for (int i = 0; i < NT; i++) pthread_create(&th[i], NULL, lazy_worker, NULL);
  long good = 0;
  for (int i = 0; i < NT; i++) { pthread_join(th[i], &res[i]); good += (long)res[i]; }
  printf("lazy_race: %ld of %d threads ok\n", good, NT);
  return good != NT;
}

/* P-21: NULL-аргументы и чужие указатели вместо дескриптора — ошибка, а не SIGSEGV. */
static int t_null_safe(const char* path) {
  char* doc = read_all(path);
  void* m = figram_load_doc(doc);
  char* r;
  r = figram_check_json(NULL); ok_if(r && !strncmp(r, "error", 5), "check_json(NULL) → error text", r); figram_free(r);
  r = figram_fmt_json(NULL); ok_if(r && !strncmp(r, "error", 5), "fmt_json(NULL) → error text", r); figram_free(r);
  ok_if(figram_load_doc(NULL) == NULL, "load_doc(NULL) → NULL", err_or("(no reason)"));
  r = figram_request_json(m, NULL); ok_if(is_error_code(r, "bad_request", 1), "request_json(h, NULL) → bad_request", r); figram_free(r);
  r = figram_request_json(NULL, HELLO); ok_if(is_error_code(r, "bad_request", 1), "request_json(NULL, line) → bad_request", r); figram_free(r);
  static long junk[16];
  r = figram_request_json(junk, HELLO); ok_if(is_error_code(r, "bad_request", 1), "request_json(foreign pointer) → bad_request", r); figram_free(r);
  figram_unload(junk); ok_if(1, "unload(foreign pointer) returned", err_or(NULL));
  if (HAVE(figram_load_doc_ex)) {
    char* e = (char*)1;
    void* h = figram_load_doc_ex(NULL, &e);
    ok_if(h == NULL && e && e != (char*)1, "load_doc_ex(NULL, &err) → NULL + reason", e != (char*)1 ? e : NULL);
    if (e && e != (char*)1) figram_free(e);
  }
  if (HAVE(figram_request_json_n)) {
    r = figram_request_json_n(m, NULL, 5); ok_if(is_error_code(r, "bad_request", 1), "request_json_n(h, NULL, 5) → bad_request", r); figram_free(r);
  }
  if (HAVE(figram_check_json_n)) {
    r = figram_check_json_n(NULL, 3); ok_if(r && !strncmp(r, "error", 5), "check_json_n(NULL, 3) → error text", r); figram_free(r);
  }
  /* После всех ошибок автомат работает. */
  r = figram_request_json(m, HELLO); ok_if(r && strstr(r, "\"states\":11"), "handle still works", r); figram_free(r);
  figram_unload(m);
  return n_fail != 0;
}

/* P-21: use-after-unload и двойная выгрузка обнаруживаются (в пределах карантина). */
static int t_lifetime(const char* path, const char* other) {
  char* doc = read_all(path);
  void* m = figram_load_doc(doc);
  char* want = figram_request_json(m, REQ);
  figram_unload(m);
  void* k = figram_load_doc(read_all(other));   /* память могла бы достаться другому автомату */
  char* r = figram_request_json(m, HELLO);
  ok_if(is_error_code(r, "bad_request", 1), "request after unload → bad_request", r);
  figram_free(r);
  figram_unload(m);                              /* двойная выгрузка */
  const char* e = figram_last_error ? figram_last_error() : NULL;
  ok_if(e && *e, "double unload is detected (figram_last_error)", e);
  r = figram_request_json(k, HELLO);
  ok_if(r && strstr(r, "\"protocol\":1"), "other handle unaffected", r);
  figram_free(r); figram_unload(k);
  long bad = 0;
  for (int i = 0; i < 2000; i++) {
    void* h = figram_load_doc(doc);
    char* q = figram_request_json(h, REQ);
    if (!q || strcmp(q, want)) bad++;
    figram_free(q); figram_unload(h);
  }
  char msg[64]; snprintf(msg, sizeof msg, "mismatches %ld", bad);
  ok_if(bad == 0, "2000 load/request/unload after the double unload", msg);
  return n_fail != 0;
}

/* P-21: функции с длиной отвергают встроенный NUL; P-21/NP-06: load_doc_ex даёт причину;
   выход: в тексте ошибки нет сырого NUL. */
static int t_lengths(const char* path) {
  char* doc = read_all(path);
  size_t n = strlen(doc);
  char* glued = malloc(n + 32);
  memcpy(glued, doc, n); memcpy(glued + n, "\0garbage{{{", 12);
  size_t gl = n + 11;
  if (HAVE(figram_load_doc_n)) {
    void* h = figram_load_doc_n(glued, gl);
    ok_if(h == NULL, "load_doc_n(doc + NUL + garbage) → NULL", err_or("(no reason)"));
    if (h) figram_unload(h);
    h = figram_load_doc_n(doc, n);
    ok_if(h != NULL, "load_doc_n(doc, len) loads", NULL);
    if (h) {
      char* r = figram_request_json(h, HELLO); ok_if(r && strstr(r, "\"states\":11"), "load_doc_n handle works", r); figram_free(r);
      if (HAVE(figram_request_json_n)) {
        const char* q = "{\"id\":5,\"op\":\"hello\"}\0{\"id\":6,\"op\":\"start\"}";
        r = figram_request_json_n(h, q, 41);
        ok_if(is_error_code(r, "parse_error", 1), "request_json_n with embedded NUL → parse_error", r); figram_free(r);
        char unterminated[21]; memcpy(unterminated, "{\"id\":5,\"op\":\"hello\"}", 21);
        r = figram_request_json_n(h, unterminated, 21);
        ok_if(r && strstr(r, "\"id\":5") && strstr(r, "\"protocol\":1"), "request_json_n without terminating NUL", r); figram_free(r);
      }
      figram_unload(h);
    }
  }
  if (HAVE(figram_check_json_n)) {
    char* r = figram_check_json_n(glued, gl);
    ok_if(r && strncmp(r, "ok", 2) && strstr(r, "NUL"), "check_json_n(doc + NUL + garbage) → error about NUL", r); figram_free(r);
    r = figram_check_json_n(doc, n); ok_if(r && !strcmp(r, "ok"), "check_json_n(doc, len) → ok", r); figram_free(r);
  }
  if (HAVE(figram_fmt_json_n)) {
    char* r = figram_fmt_json_n(glued, gl);
    ok_if(r && !strncmp(r, "error", 5), "fmt_json_n(doc + NUL + garbage) → error", r); figram_free(r);
  }
  if (HAVE(figram_load_doc_ex)) {
    char* e = NULL;
    void* h = figram_load_doc_ex("{\"figram\":1}", &e);
    ok_if(h == NULL && e && strstr(e, "bot"), "load_doc_ex(bad) → NULL + reason", e);
    figram_free(e); e = (char*)1;
    h = figram_load_doc_ex(doc, &e);
    ok_if(h != NULL && e == NULL, "load_doc_ex(good) → handle, *err = NULL", NULL);
    figram_unload(h);
    h = figram_load_doc_ex("{\"figram\":1}", NULL);
    ok_if(h == NULL, "load_doc_ex(bad, NULL) → NULL", NULL);
  }
  const char* dup_key = "{\"figram\":1,\"a\\u0000b\":1,\"a\\u0000b\":2}";
  char* r = figram_check_json(dup_key);
  ok_if(r && strstr(r, "a\\u0000b"), "check text: NUL in a key is escaped, not raw", r);
  figram_free(r);
  return n_fail != 0;
}

/* P-18: невалидный UTF-8 → parse_error (запрос) / отказ (документ), без замены на U+FFFD. */
static int t_utf8_strict(const char* path) {
  char* doc = read_all(path);
  void* m = figram_load_doc(doc);
  const char* bad[] = {
    "{\"id\":1,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\"},\"event\":\"btn:order\xff\"}",
    "{\"id\":2,\"op\":\"step\",\"snapshot\":{\"at\":\"menu\xc3\"},\"event\":\"btn:order\"}",
    "{\"id\":3,\"op\":\"hello\"}\xff",
    "{\"id\":4,\"op\":\"hel\xedlo\"}",
    "{\"id\":\"\xed\xa0\x80\",\"op\":\"hello\"}",                 /* суррогат */
    "{\"id\":\"\xc0\xaf\",\"op\":\"hello\"}",                     /* overlong */
    "{\"id\":\"\xf4\x90\x80\x80\",\"op\":\"hello\"}",             /* > U+10FFFF */
  };
  for (unsigned i = 0; i < sizeof bad / sizeof *bad; i++) {
    char* r = figram_request_json(m, bad[i]);
    char what[64]; snprintf(what, sizeof what, "invalid UTF-8 request %u → parse_error, id null", i + 1);
    ok_if(is_error_code(r, "parse_error", 1), what, r);
    figram_free(r);
  }
  char* r = figram_request_json(m, "{\"id\":\"пример ✓ \xf0\x9f\x8d\x95\",\"op\":\"hello\"}");
  ok_if(r && strstr(r, "\"protocol\":1") && strstr(r, "\xf0\x9f\x8d\x95"), "valid multi-byte UTF-8 request works", r);
  figram_free(r);
  /* Документ с невалидным UTF-8: CLI отвергает такие файлы; FFI — тоже. */
  size_t n = strlen(doc);
  char* bdoc = malloc(n + 1); memcpy(bdoc, doc, n + 1);
  char* p = strstr(bdoc, "\"menu\"");          /* испортить байт внутри строки */
  if (p) p[2] = (char)0xff;
  r = figram_check_json(bdoc);
  ok_if(r && strncmp(r, "ok", 2) && strstr(r, "UTF-8"), "check_json(invalid UTF-8 doc) → error about UTF-8", r);
  figram_free(r);
  void* h = figram_load_doc(bdoc);
  ok_if(h == NULL, "load_doc(invalid UTF-8 doc) → NULL", err_or(NULL));
  if (h) figram_unload(h);
  r = figram_fmt_json(bdoc);
  ok_if(r && !strncmp(r, "error", 5), "fmt_json(invalid UTF-8 doc) → error", r);
  figram_free(r);
  figram_unload(m);
  return n_fail != 0;
}

/* P-18/PROTOCOL_CHANGES 5, 8: предел размера запроса и пустой запрос. */
/* {"id":1,"op":"hello"   …пробелы…   } ровно n байт: корректный строгий запрос (лишних полей
   нет — PROTOCOL_CHANGES 3), размер набирается пробелами между значением и `}`. */
static char* req_of_size(size_t n) {
  const char* head = "{\"id\":1,\"op\":\"hello\"";
  size_t h = strlen(head);
  char* b = malloc(n + 1);
  memcpy(b, head, h);
  memset(b + h, ' ', n - h - 1);
  b[n - 1] = '}'; b[n] = 0;
  return b;
}
static int t_limit(const char* path) {
  void* m = figram_load_doc(read_all(path));
  char* r = figram_request_json(m, "");
  ok_if(is_error_code(r, "parse_error", 1), "empty request → parse_error", r);
  figram_free(r);
  char* at = req_of_size(1048576), *over = req_of_size(1048577);
  r = figram_request_json(m, at);
  ok_if(r && strstr(r, "\"protocol\":1"), "request of exactly 1 MiB is accepted (hello)", r); figram_free(r);
  r = figram_request_json(m, over);
  ok_if(is_error_code(r, "too_large", 1), "request of 1 MiB + 1 → too_large (default limit)", r); figram_free(r);
  if (HAVE(figram_set_max_request)) {
    size_t prev = figram_set_max_request(100);
    ok_if(prev == 1048576, "default limit is 1048576", NULL);
    char* a = req_of_size(100), *b = req_of_size(101);
    r = figram_request_json(m, a); ok_if(r && strstr(r, "\"protocol\":1"), "limit 100: 100 bytes accepted", r); figram_free(r);
    r = figram_request_json(m, b); ok_if(is_error_code(r, "too_large", 1), "limit 100: 101 bytes → too_large", r); figram_free(r);
    if (HAVE(figram_request_json_n)) {
      r = figram_request_json_n(m, b, 101); ok_if(is_error_code(r, "too_large", 1), "limit 100: request_json_n 101 bytes → too_large", r); figram_free(r);
    }
    ok_if(figram_set_max_request(0) == 100, "set_max_request returns the previous limit", NULL);
    char* big = req_of_size(5u << 20);
    r = figram_request_json(m, big); ok_if(r && strstr(r, "\"protocol\":1"), "limit 0 (unlimited): 5 MiB request accepted", r); figram_free(r);
    figram_set_max_request(1048576);
  }
  figram_unload(m);
  return n_fail != 0;
}

/* P-22: figram_init сохраняет SIGPIPE хоста и sigaltstack вызывающего потока. */
static void host_handler(int s) { (void)s; }
static int t_signals(const char* mode) {
  int custom = !strcmp(mode, "custom");
  struct sigaction sa; memset(&sa, 0, sizeof sa);
  sa.sa_handler = host_handler; sa.sa_flags = SA_RESTART;
  if (custom) sigaction(SIGPIPE, &sa, NULL);
  stack_t st; memset(&st, 0, sizeof st);
  if (custom) {
    st.ss_size = 1 << 16; st.ss_sp = malloc(st.ss_size); st.ss_flags = 0;
    sigaltstack(&st, NULL);
  }
  struct sigaction p0; sigaction(SIGPIPE, NULL, &p0);
  stack_t s0; sigaltstack(NULL, &s0);
  if (figram_init()) { ok_if(0, "figram_init", err_or(NULL)); return 1; }
  struct sigaction p1; sigaction(SIGPIPE, NULL, &p1);
  stack_t s1; sigaltstack(NULL, &s1);
  char d[256];
  snprintf(d, sizeof d, "before %p flags %d, after %p flags %d", (void*)p0.sa_handler, p0.sa_flags,
           (void*)p1.sa_handler, p1.sa_flags);
  /* SA_RESTORER glibc добавляет при каждой установке обработчика — это не изменение диспозиции. */
#ifndef SA_RESTORER
#define SA_RESTORER 0x04000000
#endif
  int same_mask = 1;
  for (int sig = 1; sig < 32; sig++) same_mask &= sigismember(&p0.sa_mask, sig) == sigismember(&p1.sa_mask, sig);
  ok_if(p0.sa_handler == p1.sa_handler && (p0.sa_flags & ~SA_RESTORER) == (p1.sa_flags & ~SA_RESTORER) && same_mask,
        "SIGPIPE disposition preserved", d);
  snprintf(d, sizeof d, "before sp=%p size=%zu flags=%d, after sp=%p size=%zu flags=%d",
           s0.ss_sp, s0.ss_size, s0.ss_flags, s1.ss_sp, s1.ss_size, s1.ss_flags);
  ok_if(s0.ss_flags == s1.ss_flags && ((s0.ss_flags & SS_DISABLE) || (s0.ss_sp == s1.ss_sp && s0.ss_size == s1.ss_size)),
        "sigaltstack of the calling thread preserved", d);
  char* r = figram_check_json("{}"); ok_if(r != NULL, "library works after init", r); figram_free(r);
  return n_fail != 0;
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
  if (!strcmp(t, "rlimit_init")) return t_rlimit_init();
  if (!strcmp(t, "lazy") && argc > 2) return t_lazy(argv[2]);
  if (!strcmp(t, "lazy_race") && argc > 2) return t_lazy_race(argv[2]);
  if (!strcmp(t, "null_safe") && argc > 2) return t_null_safe(argv[2]);
  if (!strcmp(t, "lifetime") && argc > 3) return t_lifetime(argv[2], argv[3]);
  if (!strcmp(t, "lengths") && argc > 2) return t_lengths(argv[2]);
  if (!strcmp(t, "utf8_strict") && argc > 2) return t_utf8_strict(argv[2]);
  if (!strcmp(t, "limit") && argc > 2) return t_limit(argv[2]);
  if (!strcmp(t, "signals") && argc > 2) return t_signals(argv[2]);
  return usage();
}
