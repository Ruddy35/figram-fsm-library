import Lake
open System Lake DSL

package figram where
  version := v!"0.1.0"

@[default_target]
lean_lib Figram

/-! Системные размеры рантайма Lean для исполняемых файлов (P-09): стек потоков рантайма —
`ulimit -s`, арена mimalloc не резервируется заранее (ffi/figram_rt.c); выставляются конструктором до main
(ffi/figram_exe.c). Рантайм запускается без цикла событий libuv (P-22, ffi/figram_init.cpp; обработчик
переполнения стека у исполняемых файлов сохраняется). C-ABI делает то же в figram_init (ffi/build.sh). -/

input_file figram_rt.c where
  path := "ffi" / "figram_rt.c"
  text := true

input_file figram_exe.c where
  path := "ffi" / "figram_exe.c"
  text := true

input_file figram_init.cpp where
  path := "ffi" / "figram_init.cpp"
  text := true

target figram_rt.o pkg : FilePath := do
  buildO (pkg.buildDir / "c" / "figram_rt.o") (← figram_rt.c.fetch) #[] #[] (← getLeanc)

target figram_exe.o pkg : FilePath := do
  buildO (pkg.buildDir / "c" / "figram_exe.o") (← figram_exe.c.fetch) #[] #[] (← getLeanc)

target figram_init.o pkg : FilePath := do
  buildO (pkg.buildDir / "c" / "figram_init.o") (← figram_init.cpp.fetch) #[] #["-DFIGRAM_STACK_GUARD"] (← getLeanc)

@[default_target]
lean_exe figram where
  root := `Main
  moreLinkObjs := #[figram_rt.o, figram_exe.o, figram_init.o]

lean_lib FigramApi where
  roots := #[`Figram.Api]
  defaultFacets := #[LeanLib.staticFacet]

@[default_target]
lean_exe «figram-engine» where
  root := `EngineMain
  moreLinkObjs := #[figram_rt.o, figram_exe.o, figram_init.o]

-- Glue lemmas checked by tests/proofs (not a default target: product binaries are unchanged).
lean_lib FigramAudit where
  srcDir := "tests/proofs"
  roots := #[`FigramAudit.Glue]
