// Тонкая C-прослойка для встраивания (Go через cgo, C, C++): строки JSON на входе и выходе.
// Контракт — ffi/figram.h. Прослойка проверяет аргументы до вызова кода Lean: NULL,
// дескриптор, встроенный NUL, UTF-8, предел размера запроса; ошибки C-уровня запроса —
// ответ протокола {"id":null,"error":…,"code":…} (FORMAT.md §7).
#include <lean/lean.h>
#include "figram.h"

#define FIGRAM_API FIGRAM_EXPORT

/* Объявления из libc и рантайма: leanc официального релиза Lean компилирует с -nostdinc,
   системных заголовков нет (доступны только встроенные заголовки компилятора). */
void lean_initialize_runtime_module(void);
void lean_initialize_thread(void);
void* malloc(size_t n);
void* calloc(size_t n, size_t size);
void free(void* p);
void* memcpy(void* dst, const void* src, size_t n);
void* memchr(const void* s, int c, size_t n);
size_t strlen(const char* s);
int sched_yield(void);
/* Сигналы: структура ядра/glibc не нужна — диспозиция сохраняется в непрозрачный буфер
   (struct sigaction на Linux и macOS заведомо меньше 512 байт). */
#ifndef SIGPIPE
int sigaction(int sig, const void* act, void* old);
#define SIGPIPE 13
#endif

extern lean_object* initialize_figram_Figram_Api(uint8_t builtin);
extern lean_object* figram_check(lean_object* doc);
extern lean_object* figram_fmt(lean_object* doc);
extern lean_object* figram_load_ex(lean_object* doc);
extern lean_object* figram_request(lean_object* m, lean_object* line);
/* ffi/figram_guard.cpp: вызов с перехватом исключений C++ (0 — без исключения). */
int figram_guard_call(void (*fn)(void*), void* arg, char* err, unsigned long cap);
/* ffi/figram_rt.c: системные размеры рантайма Lean (стек потоков, арена mimalloc). */
void figram_rt_defaults(void);

/* ---------- текст ошибки: figram_last_error ---------- */

#define ERR_CAP 1024
static __thread char t_err[ERR_CAP];
static __thread int t_has_err = 0;

/* Длина префикса s[0..n), не обрывающего многобайтовый символ UTF-8. */
static size_t utf8_cut(const char* s, size_t n) {
  size_t j = n;
  while (j > 0 && ((unsigned char)s[j - 1] & 0xc0) == 0x80) j--;
  if (j == 0) return n;
  unsigned char lead = (unsigned char)s[j - 1];
  size_t len = lead < 0x80 ? 1 : (lead & 0xe0) == 0xc0 ? 2 : (lead & 0xf0) == 0xe0 ? 3 : (lead & 0xf8) == 0xf0 ? 4 : 1;
  return n - (j - 1) < len ? j - 1 : n;
}

/* Буфер с длиной: сборка коротких сообщений без snprintf. */
typedef struct { char* p; size_t n, cap; } sbuf;
static void sb_str(sbuf* b, const char* s) {
  size_t k = strlen(s);
  if (b->n + k >= b->cap) k = utf8_cut(s, b->cap - 1 - b->n);
  memcpy(b->p + b->n, s, k);
  b->n += k;
  b->p[b->n] = 0;
}
static void sb_u64(sbuf* b, unsigned long long v) {
  char d[24]; int k = 0;
  do { d[k++] = (char)('0' + v % 10); v /= 10; } while (v);
  char r[24]; for (int i = 0; i < k; i++) r[i] = d[k - 1 - i];
  r[k] = 0;
  sb_str(b, r);
}

static void set_err(const char* a, const char* b) {
  sbuf s = {t_err, 0, ERR_CAP};
  t_err[0] = 0;
  sb_str(&s, a);
  if (b) sb_str(&s, b);
  t_has_err = 1;
}

FIGRAM_API const char* figram_last_error(void) { return t_has_err ? t_err : NULL; }

/* ---------- инициализация ---------- */

/* Состояние: 0 — не начата, 1 — идёт, 2 — готово, 3 — отказ (навсегда).
   Меняется атомарно: первый вызов любой функции выполняет инициализацию, одновременные
   вызовы ждут его и получают тот же результат. */
static int init_state = 0;
static int init_rc = 0;
static char init_err[ERR_CAP];
/* Каждый чужой поток (например, поток Go) нужно один раз представить рантайму Lean. */
static __thread int thread_ready = 0;

static void enter_thread(void) {
  if (!thread_ready) { lean_initialize_thread(); thread_ready = 1; }
}

typedef struct { int ok; } init_result;
static void do_init(void* arg) {
  init_result* r = (init_result*)arg;
  figram_rt_defaults();
  lean_initialize_runtime_module();
  lean_object* res = initialize_figram_Figram_Api(1);
  r->ok = lean_io_result_is_ok(res);
  lean_dec_ref(res);
  if (r->ok) lean_io_mark_end_initialization();
}

/* Буфер под struct sigaction (выравнивание как у long double и указателей). */
typedef union { unsigned char b[512]; long double ld; void* p; long l; } sigbuf;

/* Инициализация ровно один раз; одновременные вызовы ждут первый и возвращают его результат:
   0 — готово; 1 — инициализация модуля Lean вернула ошибку; 2 — рантайм Lean не запустился
   (исключение C++ при запуске). */
FIGRAM_API int figram_init(void) {
  int s = 0;
  if (__atomic_compare_exchange_n(&init_state, &s, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
    int rc = 0;
    {
      /* P-22: рантайм Lean ставит SIGPIPE в SIG_IGN для всего процесса; хосту возвращается его
         прежняя диспозиция. Обработчик SIGSEGV/SIGBUS и sigaltstack рантайм во встроенном режиме
         не трогает, потоков и дескрипторов не создаёт (ffi/figram_init.cpp). */
      sigbuf pipe_old;
      int have_pipe = sigaction(SIGPIPE, (void*)0, pipe_old.b) == 0;
      init_result r = {0};
      char what[ERR_CAP - 64];
      what[0] = 0;
      if (figram_guard_call(do_init, &r, what, sizeof what) != 0) {
        sbuf b = {init_err, 0, ERR_CAP};
        sb_str(&b, "figram_init: рантайм Lean не запустился: ");
        sb_str(&b, what);
        rc = 2;
      } else if (!r.ok) {
        sbuf b = {init_err, 0, ERR_CAP};
        sb_str(&b, "figram_init: инициализация модуля Figram.Api вернула ошибку");
        rc = 1;
      }
      if (have_pipe) sigaction(SIGPIPE, pipe_old.b, (void*)0);
    }
    if (rc == 0) thread_ready = 1;
    init_rc = rc;
    __atomic_store_n(&init_state, rc == 0 ? 2 : 3, __ATOMIC_RELEASE);
    if (rc != 0) set_err(init_err, (void*)0);
    return rc;
  }
  while ((s = __atomic_load_n(&init_state, __ATOMIC_ACQUIRE)) == 1) sched_yield();
  if (s == 2) return 0;
  set_err(init_err, (void*)0);
  return init_rc;
}

/* Ленивая инициализация в каждой точке входа: после первого успеха — одна атомарная загрузка. */
static int ensure_init(void) {
  if (__atomic_load_n(&init_state, __ATOMIC_ACQUIRE) == 2) return 0;
  return figram_init();
}

/* ---------- строки ---------- */

/* Строгая проверка UTF-8, как в рантайме Lean (validate_utf8_one): без overlong, суррогатов
   и значений больше U+10FFFF. 1 — корректно, *chars — число символов; 0 — *bad = позиция. */
static int utf8_ok(const unsigned char* s, size_t n, size_t* chars, size_t* bad) {
  size_t i = 0, c = 0;
  while (i < n) {
    unsigned b = s[i];
    if (b < 0x80) { i++; c++; continue; }
    size_t k; unsigned r, min;
    if ((b & 0xe0) == 0xc0) { k = 1; r = b & 0x1f; min = 0x80; }
    else if ((b & 0xf0) == 0xe0) { k = 2; r = b & 0x0f; min = 0x800; }
    else if ((b & 0xf8) == 0xf0) { k = 3; r = b & 0x07; min = 0x10000; }
    else { *bad = i; return 0; }
    if (n - i <= k) { *bad = i; return 0; }
    for (size_t j = 1; j <= k; j++) {
      unsigned cb = s[i + j];
      if ((cb & 0xc0) != 0x80) { *bad = i; return 0; }
      r = (r << 6) | (cb & 0x3f);
    }
    if (r < min || r > 0x10FFFF || (r >= 0xD800 && r <= 0xDFFF)) { *bad = i; return 0; }
    i += k + 1; c++;
  }
  *chars = c;
  return 1;
}

/* Копия строки Lean для вызывающего (освобождается figram_free); сырой байт NUL в тексте
   (например, имя ключа "a\u0000b" в сообщении check) записывается как \u0000, чтобы строка C
   не обрывалась. NULL — нет памяти. */
static char* take_string(lean_object* out) {
  const char* p = lean_string_cstr(out);
  size_t n = lean_string_size(out) - 1, nul = 0;
  for (const char* q = p; (q = memchr(q, 0, (size_t)(p + n - q))) != NULL; q++) nul++;
  char* r = (char*)malloc(n + 5 * nul + 1);
  if (r != NULL) {
    if (nul == 0) memcpy(r, p, n + 1);
    else {
      size_t k = 0;
      for (size_t i = 0; i < n; i++) {
        if (p[i] == 0) { memcpy(r + k, "\\u0000", 6); k += 6; }
        else r[k++] = p[i];
      }
      r[k] = 0;
    }
  }
  lean_dec_ref(out);
  return r;
}

static char* dup_cstr(const char* a, const char* b) {
  size_t na = strlen(a), nb = b ? strlen(b) : 0;
  char* r = (char*)malloc(na + nb + 1);
  if (r == NULL) return NULL;
  memcpy(r, a, na);
  if (nb) memcpy(r + na, b, nb);
  r[na + nb] = 0;
  return r;
}

/* Проверка входного документа; 0 — ок (*out — строка Lean), иначе текст причины в why. */
static int doc_in(const char* doc, size_t len, int have_len, lean_object** out, char* why, size_t cap) {
  sbuf w = {why, 0, cap};
  why[0] = 0;
  if (doc == NULL && !(have_len && len == 0)) { sb_str(&w, "документ — NULL"); return 1; }
  if (doc == NULL) doc = "";
  if (!have_len) len = strlen(doc);
  const char* z = have_len ? (const char*)memchr(doc, 0, len) : NULL;
  if (z != NULL) {
    sb_str(&w, "документ содержит байт NUL (смещение ");
    sb_u64(&w, (unsigned long long)(z - doc));
    sb_str(&w, ")");
    return 1;
  }
  size_t chars = 0, bad = 0;
  if (!utf8_ok((const unsigned char*)doc, len, &chars, &bad)) {
    sb_str(&w, "документ не в UTF-8 (смещение ");
    sb_u64(&w, bad);
    sb_str(&w, ")");
    return 1;
  }
  enter_thread();
  *out = lean_mk_string_unchecked(doc, len, chars);
  return 0;
}

/* ---------- дескрипторы автоматов ---------- */

/* Дескриптор — указатель на ячейку из собственных блоков прослойки; блоки не освобождаются,
   поэтому проверка «это наша ячейка» безопасна для любого указателя. Выгруженная ячейка
   переиспользуется не раньше, чем через FG_QUARANTINE выгрузок: запрос и повторная выгрузка
   по выгруженному дескриптору в этом окне обнаруживаются. */
#define FG_LIVE 0x4556494cu   /* "LIVE" */
#define FG_DEAD 0x44414544u   /* "DEAD" */
#define FG_QUARANTINE 1024
#define FG_CHUNKS 40
typedef struct fg_slot {
  unsigned state;             /* 0 (не выдавалась), FG_LIVE, FG_DEAD — атомарно */
  lean_object* m;             /* атомарно; NULL после выгрузки */
  struct fg_slot* next;       /* очередь карантина */
} fg_slot;

static fg_slot* g_chunk[FG_CHUNKS];   /* блок k — 64·2^k ячеек; публикуется через g_nchunks */
static int g_nchunks = 0;
static size_t g_used_in_last = 0;
static fg_slot* g_q_head = NULL;
static fg_slot* g_q_tail = NULL;
static size_t g_q_len = 0;
static int g_lock = 0;

static void lock(void) { while (__atomic_exchange_n(&g_lock, 1, __ATOMIC_ACQUIRE)) sched_yield(); }
static void unlock(void) { __atomic_store_n(&g_lock, 0, __ATOMIC_RELEASE); }
static size_t chunk_len(int k) { return (size_t)64 << k; }

static fg_slot* slot_alloc(void) {
  fg_slot* s = NULL;
  lock();
  if (g_q_len > FG_QUARANTINE) {
    s = g_q_head; g_q_head = s->next;
    if (g_q_head == NULL) g_q_tail = NULL;
    g_q_len--;
  } else {
    int n = g_nchunks;
    if (n == 0 || g_used_in_last == chunk_len(n - 1)) {
      fg_slot* c = n < FG_CHUNKS ? (fg_slot*)calloc(chunk_len(n), sizeof(fg_slot)) : NULL;
      if (c != NULL) {
        g_chunk[n] = c;
        g_used_in_last = 0;
        __atomic_store_n(&g_nchunks, n + 1, __ATOMIC_RELEASE);
        n++;
      }
    }
    if (n > 0 && g_used_in_last < chunk_len(n - 1)) s = &g_chunk[n - 1][g_used_in_last++];
  }
  unlock();
  return s;
}

static void slot_release(fg_slot* s) {
  lock();
  s->next = NULL;
  if (g_q_tail) g_q_tail->next = s; else g_q_head = s;
  g_q_tail = s;
  g_q_len++;
  unlock();
}

/* Ячейка, если p указывает ровно на одну из наших ячеек, иначе NULL. */
static fg_slot* slot_of(const void* p) {
  int n = __atomic_load_n(&g_nchunks, __ATOMIC_ACQUIRE);
  uintptr_t a = (uintptr_t)p;
  for (int k = 0; k < n; k++) {
    uintptr_t base = (uintptr_t)g_chunk[k];
    if (a >= base && a < base + chunk_len(k) * sizeof(fg_slot)) {
      if ((a - base) % sizeof(fg_slot) != 0) return NULL;
      return (fg_slot*)p;
    }
  }
  return NULL;
}

/* ---------- ответы протокола C-уровня ---------- */

static char* err_reply(const char* code, const char* msg, unsigned long long num, const char* tail) {
  char text[512];
  sbuf t = {text, 0, sizeof text};
  text[0] = 0;
  sb_str(&t, msg);
  if (tail) { sb_u64(&t, num); sb_str(&t, tail); }
  set_err(text, (void*)0);
  size_t need = 40 + t.n + strlen(code);
  char* r = (char*)malloc(need);
  if (r == NULL) return NULL;
  sbuf o = {r, 0, need};
  r[0] = 0;
  sb_str(&o, "{\"id\":null,\"error\":\"");
  sb_str(&o, text);
  sb_str(&o, "\",\"code\":\"");
  sb_str(&o, code);
  sb_str(&o, "\"}");
  return r;
}

static size_t g_max_request = 1048576;   /* PROTOCOL_CHANGES 5: 1 МиБ; 0 — без предела */

FIGRAM_API size_t figram_set_max_request(size_t max_bytes) {
  return __atomic_exchange_n(&g_max_request, max_bytes, __ATOMIC_ACQ_REL);
}

/* ---------- функции ABI ---------- */

static char* text_fn(lean_object* (*fn)(lean_object*), const char* doc, size_t len, int have_len) {
  if (ensure_init() != 0) return dup_cstr("error: ", init_err);
  char why[256];
  lean_object* s;
  if (doc_in(doc, len, have_len, &s, why, sizeof why)) { set_err(why, (void*)0); return dup_cstr("error: ", why); }
  return take_string(fn(s));
}

FIGRAM_API char* figram_check_json(const char* doc) { return text_fn(figram_check, doc, 0, 0); }
FIGRAM_API char* figram_check_json_n(const char* doc, size_t len) { return text_fn(figram_check, doc, len, 1); }
FIGRAM_API char* figram_fmt_json(const char* doc) { return text_fn(figram_fmt, doc, 0, 0); }
FIGRAM_API char* figram_fmt_json_n(const char* doc, size_t len) { return text_fn(figram_fmt, doc, len, 1); }

/* Загрузка документа. Автомат помечается многопоточным (lean_mark_mt): подсчёт ссылок для
   него атомарный, поэтому его можно одновременно использовать из любых потоков и освободить
   через figram_unload, когда он больше не нужен. Возвращает дескриптор, которым владеет
   вызывающий, или NULL; причина — в *err (если err не NULL; освобождать figram_free) и в
   figram_last_error(). */
static void* load_impl(const char* doc, size_t len, int have_len, char** err) {
  if (err) *err = NULL;
  if (ensure_init() != 0) { if (err) *err = dup_cstr(init_err, (void*)0); return NULL; }
  char why[256];
  lean_object* s;
  if (doc_in(doc, len, have_len, &s, why, sizeof why)) {
    set_err(why, (void*)0);
    if (err) *err = dup_cstr(why, (void*)0);
    return NULL;
  }
  lean_object* o = figram_load_ex(s);
  if (lean_obj_tag(o) == 0) {                  /* Except.error: текст ошибок через \n */
    lean_object* e = lean_ctor_get(o, 0);
    lean_inc(e);
    lean_dec(o);
    char* t = take_string(e);
    set_err(t ? t : "документ не загружен (нет памяти)", (void*)0);
    if (err) *err = t; else free(t);
    return NULL;
  }
  lean_object* m = lean_ctor_get(o, 0);
  lean_inc(m);
  lean_dec(o);
  lean_mark_mt(m);
  fg_slot* slot = slot_alloc();
  if (slot == NULL) {
    lean_dec(m);
    set_err("figram_load_doc: нет памяти под дескриптор", (void*)0);
    if (err) *err = dup_cstr(t_err, (void*)0);
    return NULL;
  }
  __atomic_store_n(&slot->m, m, __ATOMIC_RELAXED);
  __atomic_store_n(&slot->state, FG_LIVE, __ATOMIC_RELEASE);
  return slot;
}

FIGRAM_API void* figram_load_doc(const char* doc) { return load_impl(doc, 0, 0, NULL); }
FIGRAM_API void* figram_load_doc_n(const char* doc, size_t len) { return load_impl(doc, len, 1, NULL); }
FIGRAM_API void* figram_load_doc_ex(const char* doc, char** err) { return load_impl(doc, 0, 0, err); }
FIGRAM_API void* figram_load_doc_ex_n(const char* doc, size_t len, char** err) { return load_impl(doc, len, 1, err); }

/* Запрос протокола движка (см. Figram/Engine.lean) к загруженному автомату. Функция Lean
   забирает владение аргументом, поэтому на время вызова берётся ещё одна ссылка
   (атомарно); дескриптор вызывающего остаётся действительным. */
static char* request_impl(void* machine, const char* line, size_t len, int have_len) {
  if (machine == NULL) return err_reply("bad_request", "дескриптор автомата — NULL", 0, NULL);
  fg_slot* s = slot_of(machine);
  if (s == NULL) return err_reply("bad_request", "указатель не является дескриптором автомата (figram_load_doc)", 0, NULL);
  lean_object* m = __atomic_load_n(&s->state, __ATOMIC_ACQUIRE) == FG_LIVE ? __atomic_load_n(&s->m, __ATOMIC_ACQUIRE) : NULL;
  if (m == NULL) return err_reply("bad_request", "автомат уже выгружен (figram_unload)", 0, NULL);
  if (line == NULL && !(have_len && len == 0)) return err_reply("bad_request", "запрос — NULL", 0, NULL);
  if (line == NULL) line = "";
  if (!have_len) len = strlen(line);
  if (len == 0) return err_reply("parse_error", "пустой запрос", 0, NULL);
  size_t max = __atomic_load_n(&g_max_request, __ATOMIC_RELAXED);
  if (max != 0 && len > max)
    return err_reply("too_large", "запрос длиннее предела ", max, " байт");   /* тот же текст, что у figram-engine */
  const char* z = have_len ? (const char*)memchr(line, 0, len) : NULL;
  if (z != NULL) return err_reply("parse_error", "запрос содержит байт NUL (смещение ", (unsigned long long)(z - line), ")");
  size_t chars = 0, bad = 0;
  if (!utf8_ok((const unsigned char*)line, len, &chars, &bad))
    return err_reply("parse_error", "запрос не в кодировке UTF-8", 0, NULL);   /* как у figram-engine */
  enter_thread();
  lean_inc(m);
  return take_string(figram_request(m, lean_mk_string_unchecked(line, len, chars)));
}

FIGRAM_API char* figram_request_json(void* machine, const char* line) { return request_impl(machine, line, 0, 0); }
FIGRAM_API char* figram_request_json_n(void* machine, const char* line, size_t len) {
  return request_impl(machine, line, len, 1);
}

/* Освободить автомат; NULL допускается и ничего не делает. Чужой указатель и повторная
   выгрузка (в окне карантина) обнаруживаются: ничего не делается, причина — в
   figram_last_error(). Одновременно с выгрузкой запросы к тому же дескриптору недопустимы
   (обёртка Go обеспечивает это через RWMutex). */
FIGRAM_API void figram_unload(void* machine) {
  if (machine == NULL) return;                   /* как free(NULL) */
  fg_slot* s = slot_of(machine);
  if (s == NULL) { set_err("figram_unload: указатель не является дескриптором автомата", (void*)0); return; }
  unsigned live = FG_LIVE;
  if (!__atomic_compare_exchange_n(&s->state, &live, FG_DEAD, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
    set_err("figram_unload: автомат уже выгружен (повторная выгрузка)", (void*)0);
    return;
  }
  lean_object* m = __atomic_exchange_n(&s->m, NULL, __ATOMIC_ACQ_REL);
  enter_thread();
  lean_dec(m);
  slot_release(s);
}

FIGRAM_API void figram_free(char* s) { free(s); }
