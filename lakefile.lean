import Lake
open System Lake DSL

package figram where
  version := v!"0.1.0"

@[default_target]
lean_lib Figram

/-! Системные размеры рантайма Lean для исполняемых файлов (P-09): стек потоков рантайма —
`ulimit -s`, арена mimalloc не резервируется заранее (ffi/figram_rt.c); выставляются конструктором до main
(ffi/figram_exe.c). C-ABI вызывает то же из figram_init (ffi/build.sh). -/

input_file figram_rt.c where
  path := "ffi" / "figram_rt.c"
  text := true

input_file figram_exe.c where
  path := "ffi" / "figram_exe.c"
  text := true

target figram_rt.o pkg : FilePath := do
  buildO (pkg.buildDir / "c" / "figram_rt.o") (← figram_rt.c.fetch) #[] #[] (← getLeanc)

target figram_exe.o pkg : FilePath := do
  buildO (pkg.buildDir / "c" / "figram_exe.o") (← figram_exe.c.fetch) #[] #[] (← getLeanc)

@[default_target]
lean_exe figram where
  root := `Main
  moreLinkObjs := #[figram_rt.o, figram_exe.o]

lean_lib FigramApi where
  roots := #[`Figram.Api]
  defaultFacets := #[LeanLib.staticFacet]

@[default_target]
lean_exe «figram-engine» where
  root := `EngineMain
  moreLinkObjs := #[figram_rt.o, figram_exe.o]

-- Audit proposals (not a default target: product binaries are unchanged).
lean_lib FigramAudit where
  srcDir := "audit/proofs"
  roots := #[`FigramAudit.Glue]
