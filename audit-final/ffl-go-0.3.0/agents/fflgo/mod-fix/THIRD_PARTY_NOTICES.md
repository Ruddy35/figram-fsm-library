# Third-party notices

ffl-go itself is licensed under the MIT License ([LICENSE](LICENSE)).

The directory `lib/linux_amd64` contains the static libraries of the Figram release named in
`lib/linux_amd64/VERSION`, copied unchanged from its archive `libfigram-<version>-linux-x86_64.tar.gz`
(`scripts/update-lib.sh`; their checksums are those of the release, `lib/linux_amd64/SHA256SUMS.release`):

| File | Component | License (SPDX) |
|---|---|---|
| `libfigram.a` | Figram engine and C ABI | `MIT` |
| `libInit.a`, `libleanrt.a` | Lean 4 core library and runtime (with mimalloc) | `Apache-2.0` (mimalloc: `MIT`) |
| `libuv.a` | libuv | `MIT`, a few files `ISC` |
| `libgmp.a` | GNU MP | `LGPL-3.0-or-later OR GPL-2.0-or-later` |
| `libc++.a`, `libc++abi.a` | LLVM libc++ and libc++abi | `Apache-2.0 WITH LLVM-exception`; older code `NCSA` |

A program built with this module links the parts of these libraries it uses. The full notices,
copyright lines and license texts of the release are in
[`lib/linux_amd64/THIRD_PARTY_NOTICES.md`](lib/linux_amd64/THIRD_PARTY_NOTICES.md).

## GNU MP and the LGPL

Figram uses GNU MP under the GNU LGPL version 3. To use a modified GNU MP with a program built
with this module, relink it: replace `lib/linux_amd64/libgmp.a` (for example in the `vendor`
directory after `go mod vendor`) or build with the tag `figram_system` against a library and a
GNU MP of your choice. The source of GNU MP 6.3.0 is published at
<https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz>; the release notices above say how the bundled
copy was built.
