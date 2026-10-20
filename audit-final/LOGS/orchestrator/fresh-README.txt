FRESH reference build of audit/baseline d69bbaf with Lean 4.35.0 built from source
(tag commit eb6cafb7, gcc 13 / libstdc++; official releases use clang + libc++, so hashes differ
from bin/ — expected; CI run 39330382243 proved bin/ == official fresh build).
Logs: /home/user/figram-audit/LOGS/3x-*.log (lake build: 0 warnings).

  source /home/user/figram-audit/env.sh            # lean, lake, leanc on PATH
  figram, figram-engine                            # unstripped fresh binaries (dyn: libstdc++, gmp, uv)
  lake-cache/                                      # copy to <worktree>/.lake for fast incremental builds
  ffi-out/libfigram.a                              # ffi/build.sh output (figram_ffi.o + IR objects)
  ffi-out/lean/{libInit.a,libleanrt.a,libuv.a,libgmp.a}
  ffi-out/lean/{libc++.a,libc++abi.a}              # EMPTY stub archives so the hardcoded
                                                   # `-Wl,-Bstatic -lc++ -lc++abi` in go-example links

C link:  gcc ... -L$F/ffi-out -l:libfigram.a -L$F/ffi-out/lean -Wl,--start-group -lInit -lleanrt \
         -Wl,--end-group -lstdc++ -luv -lgmp -lm -ldl -lpthread -lrt
Go:      cp -r $F/ffi-out/* <worktree>/ffi/out/   (so ffi/out/libfigram.a and ffi/out/lean/* exist)
         cd ffi/go-example && CGO_LDFLAGS=-lstdc++ go build [-race] .
Verified: C harness plain+ASan/UBSan (LOGS/35-*), go build/-race/test -race (LOGS/36-*) all exit 0.
Original ffi/build.sh fails on this toolchain (LOGS/34-ffi-build-original.log) — BUILD finding.
