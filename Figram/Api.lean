import Figram.Engine

/-! The external API for embedding (C, Go through cgo): JSON strings in and out.
The C ABI contract is `ffi/figram.h`; the shim `ffi/figram_ffi.c` checks the arguments
(NULL, NUL, UTF-8, the request size limit) before calling these functions. -/

namespace Figram.Api
open Figram.Format

/-- Checking a document: `ok`, or the errors separated by newlines. -/
@[export figram_check]
def check (doc : String) : String :=
  match parse doc >>= decode with
  | .error e => "error: " ++ e
  | .ok d => let errs := d.check; if errs.isEmpty then "ok" else "\n".intercalate errs

/-- The canonical form of a document. -/
@[export figram_fmt]
def fmt (doc : String) : String :=
  match parse doc >>= decode with
  | .error e => "error: " ++ e
  | .ok d => print (encode d)

/-- Loading a document: the machine or `none` (the older export; the shim uses `loadEx`). -/
@[export figram_load]
def load (doc : String) : Option Machine :=
  match Engine.load doc with
  | .ok m => some m
  | .error _ => none

/-- Loading a document with the reason of a refusal: the machine, or the errors of `Engine.load`
separated by newlines (`figram_load_doc_ex`). The shim marks the machine as multi-threaded
(`lean_mark_mt`: atomic reference counting), so it can be used from any threads at the
same time; `figram_unload` releases it when the handle is no longer needed. -/
@[export figram_load_ex]
def loadEx (doc : String) : Except String Machine :=
  match Engine.load doc with
  | .ok m => .ok m
  | .error es => .error ("\n".intercalate es)

/-- An engine protocol request to a loaded machine (`Engine.handle`, docs/format.md, section 8):
the same strict field check, error reply `{id, error, code}` and `id` echo on a parse error as
in the `figram-engine` process. The shim checks an empty request, the size limit and UTF-8
before the call (its reply has `id: null`). -/
@[export figram_request]
def request (m : Machine) (line : String) : String := Engine.handle m line

end Figram.Api
