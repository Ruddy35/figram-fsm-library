#!/usr/bin/env bash
# Reproducible release packaging (Linux x86_64).
#
#   ci/package.sh [--allow-missing-docs] VERSION OUTDIR
#
# Packages a finished build of this tree (run first: lake build figram figram-engine && bash ffi/build.sh)
# and writes to OUTDIR:
#   figram-VERSION-linux-x86_64.tar.gz     bin/figram, bin/figram-engine (stripped), the documents,
#                                          schemas/, examples/
#   libfigram-VERSION-linux-x86_64.tar.gz  include/figram.h, lib/libfigram.a, the Lean runtime archives
#                                          collected by ffi/build.sh (lib/*.a), lib/pkgconfig/figram.pc,
#                                          share/figram/LDFLAGS, the documents
# The documents are LICENSE, THIRD_PARTY_NOTICES.md, README.md, CHANGELOG.md and docs/*.md. Their
# relative links that do not resolve inside the archive (source files such as ffi/figram.h) are
# rewritten to the repository at the packaged commit (https://github.com/OWNER/REPO/blob/COMMIT/PATH),
# so the shipped documentation stays navigable and pinned to the release.
#   SHA256SUMS                             sha256 of both archives
#   SHA256SUMS.binaries                    sha256 of the stripped figram and figram-engine, in the
#                                          format verify.sh expects in FIGRAM_SHA256SUMS
# Each archive holds one top-level directory named like the archive. The archives are byte-identical
# for the same sources, Lean toolchain and host tools (strip, tar, gzip): file order, owners, modes
# and mtimes are fixed (mtime = SOURCE_DATE_EPOCH, default: commit time of HEAD) and gzip stores no
# name or time.
#
# Linking against the library archive (prefix P = the extracted directory):
#   cc app.c $(PKG_CONFIG_PATH=P/lib/pkgconfig pkg-config --cflags --libs figram) -Wl,--gc-sections
#   cc app.c -IP/include -LP/lib $(cat P/share/figram/LDFLAGS)
# figram.pc is relocatable (${pcfiledir}) and contains only flags that cgo accepts in
# `#cgo pkg-config:`; share/figram/LDFLAGS contains no paths and, like ffi/out/LDFLAGS, ends with
# -Wl,--gc-sections.
#
# --allow-missing-docs: package even if one of the documents is missing (local testing only; the
# release workflow never passes it). Build outputs, ffi/figram.h, schemas/ and examples/ are always
# required.
set -euo pipefail
export LC_ALL=C TZ=UTC
unset TAR_OPTIONS GZIP
die() { echo "ci/package.sh: $*" >&2; exit 1; }

allow_missing_docs=0
if [ "${1:-}" = --allow-missing-docs ]; then allow_missing_docs=1; shift; fi
[ $# -eq 2 ] || die "usage: ci/package.sh [--allow-missing-docs] VERSION OUTDIR"
version=$1
mkdir -p "$2"
out=$(cd "$2" && pwd)
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

# The version is the one in lakefile.lean, so an archive name always matches its sources.
lake_version=$(sed -n 's/^[[:space:]]*version[[:space:]]*:=[[:space:]]*v!"\([^"]*\)".*$/\1/p' lakefile.lean)
[ "$(printf '%s\n' "$lake_version" | grep -c .)" -eq 1 ] || die "lakefile.lean: expected exactly one 'version := v!\"...\"' line"
[ "$version" = "$lake_version" ] || die "version $version differs from lakefile.lean ($lake_version)"
[ "$(uname -s) $(uname -m)" = "Linux x86_64" ] || die "only Linux x86_64 is packaged (this host: $(uname -sm))"

# Required inputs.
B=.lake/build/bin
for f in "$B/figram" "$B/figram-engine"; do
  [ -x "$f" ] || die "$f is missing: run lake build figram figram-engine"
done
lake build --no-build figram figram-engine >/dev/null 2>&1 \
  || die "$B is not up to date with the sources: run lake build figram figram-engine"
[ -f ffi/out/libfigram.a ] && [ -f ffi/out/LDFLAGS.runtime ] || die "ffi/out is missing: run bash ffi/build.sh"
stale=$(find ffi -maxdepth 1 -type f \( -name '*.c' -o -name '*.cpp' -o -name '*.h' \) -newer ffi/out/libfigram.a
        find .lake/build/ir -name '*.c.o.export' -newer ffi/out/libfigram.a)
[ -z "$stale" ] || die "ffi/out/libfigram.a is older than $(echo $stale): run bash ffi/build.sh"
[ -f ffi/figram.h ] || die "ffi/figram.h is missing"
if git rev-parse --git-dir >/dev/null 2>&1; then
  mapfile -t examples < <(git ls-files examples | sort)
  mapfile -t schemas < <(git ls-files schemas | sort)
  mapfile -t docpages < <(git ls-files 'docs/*.md' | sort)
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "ci/package.sh: WARNING: tracked files have uncommitted changes; the archives do not match commit $(git rev-parse --short HEAD)" >&2
  fi
else
  mapfile -t examples < <(find examples -type f | sort)
  mapfile -t schemas < <(find schemas -type f | sort)
  mapfile -t docpages < <(find docs -maxdepth 1 -name '*.md' | sort)
fi
printf '%s\n' "${examples[@]}" | grep -q '\.bot\.json$' || die "examples/ has no *.bot.json"
printf '%s\n' "${schemas[@]}" | grep -q '\.schema\.json$' || die "schemas/ has no *.schema.json"
documents=(LICENSE THIRD_PARTY_NOTICES.md README.md CHANGELOG.md "${docpages[@]}")
# Where the rewritten links point: the GitHub repository of this checkout at this commit.
repo_url=${FIGRAM_REPO_URL:-}
if [ -z "$repo_url" ] && [ -n "${GITHUB_SERVER_URL:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  repo_url=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY
fi
if [ -z "$repo_url" ]; then
  repo_url=$(git remote get-url origin 2>/dev/null | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##') || true
fi
commit=$(git rev-parse HEAD 2>/dev/null) || commit=
case "$repo_url" in https://*) ;; *) die "cannot tell the repository URL for the documentation links: set FIGRAM_REPO_URL";; esac
[ -n "$commit" ] || die "not a git checkout: the documentation links need a commit"
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH=$(git log -1 --format=%ct 2>/dev/null) || die "not a git checkout: set SOURCE_DATE_EPOCH"
fi
case "$SOURCE_DATE_EPOCH" in ''|*[!0-9]*) die "SOURCE_DATE_EPOCH must be a number of seconds";; esac

missing=()
for f in LICENSE THIRD_PARTY_NOTICES.md README.md CHANGELOG.md docs/format.md docs/embedding.md; do
  [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  [ $allow_missing_docs = 1 ] || die "missing ${missing[*]} (--allow-missing-docs only for local tests)"
  echo "ci/package.sh: WARNING: packaging without ${missing[*]} (--allow-missing-docs): not a release" >&2
fi

# Runtime link flags exactly as ffi/build.sh wrote them for this toolchain, without the absolute -L
# to ffi/out/lean. Every archive of ffi/out/lean must be named by the flags and every library named
# by the flags must be an archive of ffi/out/lean or a system library.
read -r -a rt <<< "$(cat ffi/out/LDFLAGS.runtime)"
system_libs=" m dl pthread rt stdc++ "
flags=(); referenced=()
for t in "${rt[@]}"; do
  case "$t" in
    -L"$root/ffi/out/lean") ;;
    -L*) die "ffi/out/LDFLAGS.runtime has $t, not -L$root/ffi/out/lean (written for another tree?): run bash ffi/build.sh";;
    -l:*) referenced+=("${t#-l:}"); flags+=("$t");;
    -l*) case "$system_libs" in *" ${t#-l} "*) ;; *) referenced+=("lib${t#-l}.a");; esac; flags+=("$t");;
    *) flags+=("$t");;
  esac
done
for a in "${referenced[@]}"; do
  [ -f "ffi/out/lean/$a" ] || die "ffi/out/LDFLAGS.runtime names $a, which is not in ffi/out/lean"
done
mapfile -t runtime < <(cd ffi/out/lean && ls -- *.a | sort)
for a in "${runtime[@]}"; do
  case " ${referenced[*]} " in *" $a "*) ;; *) die "ffi/out/lean/$a is not used by ffi/out/LDFLAGS.runtime (stale ffi/out?)";; esac
done
pc_flags=()
for t in "${flags[@]}"; do [ "$t" = -Wl,--gc-sections ] || pc_flags+=("$t"); done

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
put() {  # put MODE SRC DEST (DEST relative to $T)
  install -d -m 755 "$T/$(dirname "$3")"
  install -m "$1" "$2" "$T/$3"
}
put_docs() {  # put_docs DIR FILE...
  local d=$1 f; shift
  for f in "$@"; do [ -f "$f" ] && put 644 "$f" "$d/$f"; done; return 0
}

cli=figram-$version-linux-x86_64
for b in figram figram-engine; do put 755 "$B/$b" "$cli/bin/$b"; strip "$T/$cli/bin/$b"; done
put_docs "$cli" "${documents[@]}"
for f in "${schemas[@]}" "${examples[@]}"; do put 644 "$f" "$cli/$f"; done

lib=libfigram-$version-linux-x86_64
put 644 ffi/figram.h "$lib/include/figram.h"
put 644 ffi/out/libfigram.a "$lib/lib/libfigram.a"
for a in "${runtime[@]}"; do put 644 "ffi/out/lean/$a" "$lib/lib/$a"; done
install -d -m 755 "$T/$lib/lib/pkgconfig" "$T/$lib/share/figram"
cat > "$T/$lib/lib/pkgconfig/figram.pc" <<EOF
# Relocatable: paths are relative to this file. -Wl,--gc-sections is not listed because cgo rejects
# it in \`#cgo pkg-config:\`; pass it to the final link yourself (it drops unused Lean runtime code).
prefix=\${pcfiledir}/../..
includedir=\${prefix}/include
libdir=\${prefix}/lib

Name: figram
Description: Figram finite-state-machine engine for chat bots, static C ABI library
Version: $version
Cflags: -I\${includedir}
Libs: -L\${libdir} -l:libfigram.a ${pc_flags[*]}
EOF
echo "-l:libfigram.a ${flags[*]}" > "$T/$lib/share/figram/LDFLAGS"
chmod 644 "$T/$lib/lib/pkgconfig/figram.pc" "$T/$lib/share/figram/LDFLAGS"
put_docs "$lib" "${documents[@]}"

# Relative links of the shipped Markdown files: kept when the target is in the same archive,
# otherwise pointed at the repository at this commit (a directory as tree/, a file as blob/).
link_docs() {  # link_docs ARCHIVE_DIR
  python3 - "$T/$1" "$root" "$repo_url" "$commit" <<'PY'
import os, re, sys
top, root, url, commit = sys.argv[1:5]
link = re.compile(r"(\]\()([^)\s#]+)((?:#[^)\s]*)?\))")
for d, _, files in os.walk(top):
    for name in sorted(files):
        if not name.endswith(".md"):
            continue
        path = os.path.join(d, name)
        rel_dir = os.path.relpath(d, top)
        def fix(m):
            target = m.group(2)
            if re.match(r"[a-z][a-z0-9+.-]*:", target):
                return m.group(0)
            repo_path = os.path.normpath(os.path.join(rel_dir, target))
            if os.path.exists(os.path.join(top, repo_path)):
                return m.group(0)
            if repo_path.startswith("..") or not os.path.exists(os.path.join(root, repo_path)):
                sys.exit(f"{os.path.relpath(path, top)}: link {target} resolves neither in the archive nor in the repository")
            kind = "tree" if os.path.isdir(os.path.join(root, repo_path)) else "blob"
            return f"{m.group(1)}{url}/{kind}/{commit}/{repo_path}{m.group(3)}"
        text = open(path, encoding="utf-8").read()
        new = link.sub(fix, text)
        if new != text:
            open(path, "w", encoding="utf-8").write(new)
PY
}
link_docs "$cli"
link_docs "$lib"

pack() {
  (cd "$T" && tar --create --format=gnu --sort=name --owner=0 --group=0 --numeric-owner \
     --mtime="@$SOURCE_DATE_EPOCH" --mode='u+rw,go+r,go-w' --file=- "$1") | gzip -9 -n > "$out/$1.tar.gz"
}
pack "$cli"
pack "$lib"
(cd "$out" && sha256sum "$cli.tar.gz" "$lib.tar.gz" > SHA256SUMS)
(cd "$T/$cli/bin" && sha256sum figram figram-engine) > "$out/SHA256SUMS.binaries"

for a in "$cli" "$lib"; do
  echo "$a.tar.gz: $(tar -tzf "$out/$a.tar.gz" | grep -vc '/$') files, $(stat -c %s "$out/$a.tar.gz") bytes"
done
echo "runtime archives (lib/): ${runtime[*]}"
echo "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
cat "$out/SHA256SUMS" "$out/SHA256SUMS.binaries"
