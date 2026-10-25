import Lake
open System Lake DSL

package figram where
  version := v!"0.2.0"

@[default_target]
lean_lib Figram

/-! System sizes of the Lean runtime for the executables: the stack of runtime threads is
`ulimit -s` and the mimalloc arena is not reserved in advance (ffi/figram_rt.c); a constructor sets
them before main (ffi/figram_exe.c). The runtime starts without the libuv event loop (ffi/figram_init.cpp;
the executables keep the stack overflow handler). The C ABI does the same in figram_init (ffi/build.sh). -/

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
