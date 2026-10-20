// Проверка C-прослойки: невалидный UTF-8, пустые и огромные входы,
// параллельные вызовы из потоков POSIX к одному неизменяемому автомату.
// Ожидания по FORMAT.md §7 / PROTOCOL_CHANGES: ошибки C-прослойки — {"id":null,"error":…,"code":…}
// (пустой запрос и невалидный UTF-8 — parse_error, длиннее предела 1 МиБ — too_large).
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

/* Резидентная память процесса в КБ (Linux). */
static long rss_kb(void) {
  FILE* f = fopen("/proc/self/statm", "r");
  char buf[128]; long pages = 0, rss = 0;
  if (f && fgets(buf, sizeof buf, f)) sscanf(buf, "%ld %ld", &pages, &rss);
  if (f) fclose(f);
  return rss * 4;
}

/* Загрузить и выгрузить документ n раз, делая запрос к каждому; рост памяти. */
static long reload_growth(const char* doc, int n, int unload) {
  for (int i = 0; i < 50; i++) {                 /* прогрев аллокатора */
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

/* Ответ — ошибка (code "" — любая; иначе с этим кодом и id null); code NULL — не ошибка. */
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
  if (!bot) { printf("документ не загружен\n"); return 1; }
  void* bad_doc = figram_load_doc("{\"figram\": 1}");
  printf("некорректный документ: %s\n", bad_doc ? "ЗАГРУЖЕН" : "отвергнут");

  /* code: ожидаемый код ошибки C-прослойки; "" — ошибка движка (код не проверяется);
     NULL — не ошибка. */
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
    printf("вход %u → %.110s\n", i, r);
    if (!expect_error(r, nasty[i].code)) { printf("НЕТ: вход %u: ожидалось %s\n", i, nasty[i].code ? nasty[i].code : "не ошибка"); bad_inputs++; }
    figram_free(r);
  }
  unsigned long n = 5u << 20;                    // строка 5 МБ: длиннее предела 1 МиБ
  char* big = malloc(n + 64);
  memset(big, 'a', n + 64);
  big[0] = '"'; big[n] = '"'; big[n + 1] = 0;
  char* r = figram_request_json(bot, big);
  printf("строка 5 МБ → %.80s\n", r);
  if (!expect_error(r, "too_large")) { printf("НЕТ: строка 5 МБ: ожидалось too_large\n"); bad_inputs++; }
  figram_free(r); free(big);

  expected = figram_request_json(bot, REQ);
  pthread_t th[16];
  for (int i = 0; i < 16; i++) pthread_create(&th[i], 0, worker, 0);
  long bad = 0;
  for (int i = 0; i < 16; i++) { void* b; pthread_join(th[i], &b); bad += (long)b; }
  printf("16 потоков × 20000 вызовов к одному автомату: расхождений %ld\n", bad);
  figram_free(expected);

  /* Замер памяти имеет смысл только без ASan (у него карантин освобождённой памяти). */
  int reload_ok = 1;
  if (argc > 2) {
    long with_unload = reload_growth(doc, 5000, 1);
    long without_unload = reload_growth(doc, 5000, 0);
    printf("5000 загрузок и выгрузок: рост памяти %ld КБ; без выгрузки (контроль): %ld КБ\n",
           with_unload, without_unload);
    reload_ok = with_unload < 2048 && without_unload > 8192;
  }
  if (!reload_ok) printf("НЕТ: выгрузка не освобождает память или контроль нечувствителен\n");
  figram_unload(bot);
  if (bad_inputs) printf("НЕТ: %d граничных входов дали не тот ответ\n", bad_inputs);
  return bad != 0 || !reload_ok || bad_inputs != 0;
}
