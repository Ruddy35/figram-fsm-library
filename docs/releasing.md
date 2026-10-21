# Releasing

Release binaries are never committed to git. They are built only by the `release` workflow
(`.github/workflows/release.yml`) on GitHub Actions with the official Lean release named in
`lean-toolchain`, and published as a GitHub Release with SHA-256 checksums. The workflow always creates
a **draft**; a maintainer reviews the draft and publishes it.

## Cutting a release

1. Choose the version `X.Y.Z` (semantic versioning; a pre-release suffix such as `0.2.0-rc.1` is
   allowed and marks the GitHub Release as a pre-release).
2. Set it in `lakefile.lean`: `version := v!"X.Y.Z"`.
3. Add a section for it to `CHANGELOG.md`, headed `## [X.Y.Z] - YYYY-MM-DD` (`## X.Y.Z` also works;
   in a section prepared as `## [X.Y.Z] - Unreleased`, put in the date). The body of this section
   becomes the top of the release notes; the workflow refuses a version without one.
4. Merge to the default branch and wait for `ci` to pass. Its `release dry run` job already packages
   the commit, checks that two independent builds give byte-identical archives and that the archives
   are self-contained, and runs `verify-prebuilt.sh` on the packaged binaries.
5. Start the release workflow, either
   * from the Actions tab: **release → Run workflow**, pick the branch (usually the default branch;
     its head commit is released) and enter the version without `v`, or
     `gh workflow run release.yml --ref BRANCH -f version=X.Y.Z` with that branch; or
   * by pushing a tag at the commit to release: `git tag -a vX.Y.Z -m "Figram X.Y.Z" COMMIT && git push
     origin vX.Y.Z`.
6. The workflow has two jobs. The job `build, verify and package` runs with a read-only token:
   * checks the version format, that it equals `lakefile.lean`, and that `CHANGELOG.md` has its section;
   * if the tag `vX.Y.Z` already exists, checks that it points at the commit being built;
   * installs Lean with `ci/install-lean.sh` (release archive, SHA-256 pinned in
     `ci/lean-toolchain.sha256`), Go 1.23 and Python `jsonschema`;
   * builds (`lake build figram figram-engine`, `bash ffi/build.sh`), checks that the build left tracked
     files unchanged, and packages with `ci/package.sh`;
   * deletes all build outputs and runs `./verify.sh` with `FIGRAM_SHA256SUMS` set to the packaged
     `SHA256SUMS.binaries`: the full gate on a clean rebuild, whose stripped binaries must equal the
     packaged ones; exit code 0 is required;
   * packages the clean rebuild again and requires byte-identical archives;
   * runs `ci/test-bundle.sh`: checksums, the extracted CLI and engine, and C and Go programs built
     against nothing but the extracted library archive;
   * runs `./verify-prebuilt.sh` on the binaries extracted from the CLI archive;
   * writes the release notes (the CHANGELOG section, with its relative links pointing at the sources
     of the commit, a downloads table, a provenance block and the checksums) and uploads the
     archives, checksums and notes as a workflow artifact.

   Then the job `draft release`, the only one with write access (`contents: write`), checks out
   nothing and runs no code of the repository: it downloads that artifact, checks `SHA256SUMS`, and
   creates the draft release `vX.Y.Z` targeting the commit, or updates the existing draft of that tag
   (notes, target commit and assets; stale assets are removed), checking before and after each change
   that the release is still a draft.

   The workflow never publishes a release, never deletes one, and stops if a **published** release
   `vX.Y.Z` already exists (a run started by the tag of that release only compares the archives,
   step 8) or if several releases carry that tag.
7. Review the draft on the Releases page: the notes, the provenance block (commit, toolchain, workflow
   run), the four assets. Optionally download them and check them as described below.
8. Publish the draft (**Edit → Publish release**, or `gh release edit vX.Y.Z --draft=false`). If the tag
   does not exist yet, GitHub creates it at the draft's target commit. The new tag starts the workflow
   once more: it rebuilds the tag, runs the same checks and compares its `SHA256SUMS` with the
   published one; it does not change the release, and it fails if the archives differ.

Running the workflow again for the same version replaces the assets and notes of the draft. A published
version is final: fix problems with a new version.

## Release assets

| Asset | Contents |
|---|---|
| `figram-X.Y.Z-linux-x86_64.tar.gz` | `bin/figram` (CLI) and `bin/figram-engine` (line-delimited JSON engine), stripped; the documents `LICENSE`, `THIRD_PARTY_NOTICES.md`, `README.md`, `CHANGELOG.md`, `docs/*.md`; `schemas/`; `examples/` |
| `libfigram-X.Y.Z-linux-x86_64.tar.gz` | `include/figram.h`; `lib/libfigram.a` and the Lean runtime archives it needs (`lib/*.a`); `lib/pkgconfig/figram.pc`; `share/figram/LDFLAGS`; the documents `LICENSE`, `THIRD_PARTY_NOTICES.md`, `README.md`, `CHANGELOG.md`, `docs/*.md` |

In the shipped Markdown files, a relative link to a file that is not in the same archive (a source
file such as `ffi/figram.h`) is rewritten to the repository at the packaged commit
(`https://github.com/OWNER/REPO/blob/COMMIT/PATH`), so the documentation in an archive stays
navigable and matches the release.
| `SHA256SUMS` | SHA-256 of the two archives |
| `SHA256SUMS.binaries` | SHA-256 of the stripped `figram` and `figram-engine` (the format `verify.sh` reads from `FIGRAM_SHA256SUMS`) |

Each archive unpacks into one directory named like the archive. The binaries need Linux x86_64 with
glibc; `ldd bin/figram` lists the shared libraries they use.

The library supports static linking into an executable only (see `docs/embedding.md`). Its `lib/`
holds exactly the runtime archives that `ffi/build.sh` collected for the toolchain of the release:
with the official Lean release these are `libInit.a`, `libleanrt.a`, `libc++.a`, `libc++abi.a`,
`libuv.a` and `libgmp.a`. With `P` the unpacked directory:

```sh
# pkg-config (figram.pc is relocatable: its paths are relative to the .pc file)
export PKG_CONFIG_PATH=$P/lib/pkgconfig
cc app.c $(pkg-config --cflags --libs figram) -Wl,--gc-sections

# without pkg-config: share/figram/LDFLAGS holds the library flags without any path
cc app.c -I"$P/include" -L"$P/lib" $(cat "$P/share/figram/LDFLAGS")

# Go (cgo): flags from the environment, or `#cgo pkg-config: figram` in the source plus PKG_CONFIG_PATH
CGO_CFLAGS="-I$P/include" CGO_LDFLAGS="-L$P/lib $(cat "$P/share/figram/LDFLAGS")" \
  go build -ldflags=-extldflags=-Wl,--gc-sections
```

`-Wl,--gc-sections` keeps unused Lean runtime code out of the program. It is part of
`share/figram/LDFLAGS` but not of `figram.pc`, because cgo rejects it in `#cgo pkg-config:` flags.

## Verifying a download

```sh
sha256sum -c SHA256SUMS                                   # both archives
tar -xzf figram-X.Y.Z-linux-x86_64.tar.gz
(cd figram-X.Y.Z-linux-x86_64/bin && sha256sum -c ../../SHA256SUMS.binaries)
```

The checksum files are published next to the archives, so they detect damaged or mismatched
downloads, not a tampered release page. Releases are not signed. For an independent check, rebuild
from source and compare (next section).

## Reproducing a release

```sh
git clone https://github.com/Ruddy35/figram-fsm-library && cd figram-fsm-library
git checkout vX.Y.Z
export PATH="$(ci/install-lean.sh | tail -1):$PATH"   # official Lean of lean-toolchain, SHA-256 checked
lake build figram figram-engine && bash ffi/build.sh
ci/package.sh X.Y.Z /tmp/figram-dist
diff /tmp/figram-dist/SHA256SUMS /path/to/downloaded/SHA256SUMS
```

`FIGRAM_SHA256SUMS=/path/to/downloaded/SHA256SUMS.binaries ./verify.sh` runs the full gate and compares
the rebuilt binaries with the published ones in one step (it needs Go 1.23 and Python `jsonschema`).

The same bytes come out only for the same commit, the official Lean release named in `lean-toolchain`,
Linux x86_64 and the same versions of the host tools that touch the output: `strip` (GNU binutils),
GNU `tar` (1.28 or later, for `--sort=name`) and `gzip`. The provenance block of each release names
the versions that built it. `ci/package.sh` fixes everything else: file order, owner and group 0,
modes, mtimes (`SOURCE_DATE_EPOCH`, by default the commit time of `HEAD`) and gzip headers without
name or time. Packaging a tree with uncommitted changes prints a warning.

## Guarantees and their limits

For a release made by the workflow:

* **Sources.** The assets were built from the commit named in the provenance block; the tag points at
  that commit (the workflow refuses an existing tag elsewhere, and publishing creates a missing tag at
  the draft's target).
* **Toolchain.** Lean is the official release archive whose SHA-256 is pinned in
  `ci/lean-toolchain.sha256`; GitHub Actions are pinned by commit SHA.
* **Write access.** Only the job `draft release`, which runs no code of the repository, can write
  to the repository; it uploads the archives whose `SHA256SUMS` it checked.
* **Gate.** `verify.sh` (build without warnings, theorem gates, engine, protocol, client, C ABI and Go
  tests) exited 0 on a clean rebuild of that commit, and the rebuilt stripped binaries are
  byte-identical to the released ones.
* **Reproducibility.** Two independent builds in the run produced byte-identical archives; `ci` checks
  the same on every push.
* **Released files.** `verify-prebuilt.sh` passed on the binaries taken out of the CLI archive, and C
  and Go programs linked only against the unpacked library archive ran correctly.

Limits:

* Both builds of a run use the same runner image, so the check proves determinism of the build, not
  independence from the build machine; a rebuild elsewhere matches only under the conditions above.
* Provenance rests on the workflow log and the release notes, not on a signature. People with write
  access to the repository can change a draft before it is published: check that the draft's
  provenance links to a successful `release` run of the expected commit before publishing.
* The proofs are about the Lean model of the engine (`docs/proofs.md`); the C layer, the Lean
  compiler and runtime, the C toolchain and the operating system are trusted, and the tests reduce but
  do not remove that trust.
* Only Linux x86_64 is built.

## Third-party licenses

Figram is MIT-licensed (`LICENSE`). The binaries and the library archive also contain statically linked
third-party code: the Lean 4 runtime and core library (`libleanrt.a`, `libInit.a`; they include the
mimalloc allocator), libuv, GMP, LLVM libc++ and libc++abi, and in the executables also LLVM libunwind,
compiler-rt start-up objects and glibc start-up objects. Their licenses and the obligations that come
with distributing programs linked against them are listed in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), which ships in both archives.
