import Figram.Engine

/-! Внешний API для встраивания (C, Go через cgo): строки JSON на входе и выходе.
Контракт C-ABI — `ffi/figram.h`; прослойка `ffi/figram_ffi.c` проверяет аргументы
(NULL, NUL, UTF-8, предел размера запроса) до вызова этих функций. -/

namespace Figram.Api
open Figram.Format

/-- Проверка документа: «ok» или ошибки через перевод строки. -/
@[export figram_check]
def check (doc : String) : String :=
  match parse doc >>= decode with
  | .error e => "error: " ++ e
  | .ok d => let errs := d.check; if errs.isEmpty then "ok" else "\n".intercalate errs

/-- Каноническая запись документа. -/
@[export figram_fmt]
def fmt (doc : String) : String :=
  match parse doc >>= decode with
  | .error e => "error: " ++ e
  | .ok d => print (encode d)

/-- Загрузка документа: автомат или `none` (прежний экспорт; прослойка использует `loadEx`). -/
@[export figram_load]
def load (doc : String) : Option Machine :=
  match Engine.load doc with
  | .ok m => some m
  | .error _ => none

/-- Загрузка документа с причиной отказа: автомат или ошибки `Engine.load` через перевод
строки (`figram_load_doc_ex`). Прослойка помечает полученный автомат многопоточным
(`lean_mark_mt`: атомарный подсчёт ссылок), поэтому его можно одновременно использовать из
любых потоков; освобождает его `figram_unload`, когда дескриптор больше не нужен. -/
@[export figram_load_ex]
def loadEx (doc : String) : Except String Machine :=
  match Engine.load doc with
  | .ok m => .ok m
  | .error es => .error ("\n".intercalate es)

/-- Запрос протокола движка к загруженному автомату (см. `Figram.Engine`). -/
@[export figram_request]
def request (m : Machine) (line : String) : String := Engine.handle m line

end Figram.Api
