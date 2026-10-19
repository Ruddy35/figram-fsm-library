import Figram.Engine

/-! Внешний API для встраивания (C, Go через cgo): строки JSON на входе и выходе. -/

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

/-- Загрузка документа: автомат или `none`. Прослойка помечает автомат
неизменяемым (`lean_mark_persistent`), после чего его можно читать из любых
потоков одновременно без блокировок. -/
@[export figram_load]
def load (doc : String) : Option Machine :=
  match Engine.load doc with
  | .ok m => some m
  | .error _ => none

/-- Запрос протокола движка к загруженному автомату (см. `Figram.Engine`). -/
@[export figram_request]
def request (m : Machine) (line : String) : String := Engine.handle m line

end Figram.Api
