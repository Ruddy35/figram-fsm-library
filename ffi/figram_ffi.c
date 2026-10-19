// Тонкая C-прослойка для встраивания (Go через cgo, C, C++): строки JSON на входе и выходе.
#include <lean/lean.h>

#define FIGRAM_API __attribute__((visibility("default")))

/* Объявления из libc и рантайма: leanc собирает без системных заголовков. */
void lean_initialize_runtime_module(void);
void lean_initialize_thread(void);
void* malloc(size_t n);
void free(void* p);
void* memcpy(void* dst, const void* src, size_t n);
int sched_yield(void);

extern lean_object* initialize_figram_Figram_Api(uint8_t builtin);
extern lean_object* figram_check(lean_object* doc);
extern lean_object* figram_fmt(lean_object* doc);
extern lean_object* figram_load(lean_object* doc);
extern lean_object* figram_request(lean_object* m, lean_object* line);

/* Состояние инициализации: 0 — не начата, 1 — идёт, 2 — готово, 3 — отказ.
   Меняется атомарно: figram_init можно вызывать из нескольких потоков одновременно. */
static int init_state = 0;
/* Каждый чужой поток (например, поток Go) нужно один раз представить рантайму Lean. */
static __thread int thread_ready = 0;

static void enter_thread(void) {
  if (!thread_ready) { lean_initialize_thread(); thread_ready = 1; }
}

/* NULL, если памяти нет: вызывающий получает NULL вместо строки. */
static char* dup(const char* s, size_t n) {
  char* r = (char*)malloc(n + 1);
  if (r == NULL) return NULL;
  memcpy(r, s, n + 1);
  return r;
}

static char* take_string(lean_object* out) {
  char* res = dup(lean_string_cstr(out), lean_string_size(out) - 1);
  lean_dec_ref(out);
  return res;
}

/* Инициализация ровно один раз; одновременные вызовы ждут первый и
   возвращают его результат (0 — готово, 1 — отказ). */
FIGRAM_API int figram_init(void) {
  int s = 0;
  if (__atomic_compare_exchange_n(&init_state, &s, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
    lean_initialize_runtime_module();
    lean_object* r = initialize_figram_Figram_Api(1);
    int ok = lean_io_result_is_ok(r);
    lean_dec_ref(r);
    if (ok) lean_io_mark_end_initialization();
    thread_ready = 1;
    __atomic_store_n(&init_state, ok ? 2 : 3, __ATOMIC_RELEASE);
    return ok ? 0 : 1;
  }
  while ((s = __atomic_load_n(&init_state, __ATOMIC_ACQUIRE)) == 1) sched_yield();
  return s == 2 ? 0 : 1;
}

FIGRAM_API char* figram_check_json(const char* doc) { enter_thread(); return take_string(figram_check(lean_mk_string(doc))); }
FIGRAM_API char* figram_fmt_json(const char* doc) { enter_thread(); return take_string(figram_fmt(lean_mk_string(doc))); }

/* Загрузка документа. Автомат помечается многопоточным (lean_mark_mt): подсчёт
   ссылок для него атомарный, поэтому его можно одновременно использовать из любых
   потоков и освободить через figram_unload, когда он больше не нужен.
   Возвращает дескриптор, которым владеет вызывающий. */
FIGRAM_API void* figram_load_doc(const char* doc) {
  enter_thread();
  lean_object* o = figram_load(lean_mk_string(doc));
  if (lean_is_scalar(o)) return NULL;            /* Option.none */
  lean_object* m = lean_ctor_get(o, 0);
  lean_inc(m);
  lean_dec(o);
  lean_mark_mt(m);
  return m;
}

/* Запрос протокола движка (см. Figram/Engine.lean) к загруженному автомату.
   Функция Lean забирает владение аргументом, поэтому на время вызова берётся
   ещё одна ссылка (атомарно); дескриптор вызывающего остаётся действительным. */
FIGRAM_API char* figram_request_json(void* machine, const char* line) {
  enter_thread();
  lean_object* m = (lean_object*)machine;
  lean_inc(m);
  return take_string(figram_request(m, lean_mk_string(line)));
}

/* Освободить автомат; NULL допускается и ничего не делает. Вызывающий гарантирует, что одновременно с этим и после
   этого дескриптор не используется (обёртка Go — через RWMutex). */
FIGRAM_API void figram_unload(void* machine) {
  if (machine == NULL) return;                   /* как free(NULL) */
  enter_thread();
  lean_dec((lean_object*)machine);
}

FIGRAM_API void figram_free(char* s) { free(s); }
