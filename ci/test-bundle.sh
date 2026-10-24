#!/usr/bin/env bash
# Self-containment test of the release archives written by ci/package.sh.
#
#   ci/test-bundle.sh DIST VERSION [WORKDIR]
#
# Checks DIST/SHA256SUMS and DIST/SHA256SUMS.binaries, extracts both archives into WORKDIR (default:
# a temporary directory removed at exit; a given WORKDIR is kept, e.g. for verify-prebuilt.sh) and,
# with nothing but the extracted files and the system C compiler and Go:
#   1. runs bin/figram and bin/figram-engine of the CLI archive (a small document, every shipped
#      examples/*.bot.json);
#   2. compiles and runs a C program against the library archive, linked once with
#      `pkg-config --cflags --libs figram` and once with share/figram/LDFLAGS;
#   3. builds and runs a Go program with cgo against the library archive, once with
#      CGO_CFLAGS/CGO_LDFLAGS from share/figram/LDFLAGS and once with `#cgo pkg-config: figram`.
# The test programs are generated in WORKDIR; no file of the repository is read, and the programs
# run with an empty environment. Exit 0 = every check passed.
set -euo pipefail
export LC_ALL=C
die() { echo "FAIL: $*" >&2; exit 1; }
[ $# -ge 2 ] && [ $# -le 3 ] || die "usage: ci/test-bundle.sh DIST VERSION [WORKDIR]"
DIST=$(cd "$1" && pwd)
V=$2
if [ $# -eq 3 ]; then
  mkdir -p "$3"; W=$(cd "$3" && pwd)
else
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fi
cli=figram-$V-linux-x86_64
lib=libfigram-$V-linux-x86_64

echo "== checksums"
[ "$(awk '{print $2}' "$DIST/SHA256SUMS" | tr '\n' ' ')" = "$cli.tar.gz $lib.tar.gz " ] \
  || die "SHA256SUMS must list exactly $cli.tar.gz and $lib.tar.gz"
(cd "$DIST" && sha256sum --strict -c SHA256SUMS)

echo "== extract"
for a in "$cli" "$lib"; do
  # Only regular files and directories, all inside the one top-level directory.
  bad=$(tar -tvzf "$DIST/$a.tar.gz" | awk -v top="$a/" '$1 !~ /^[-d]/ || index($6, top) != 1 || $6 ~ /(^|\/)\.\.(\/|$)/')
  [ -z "$bad" ] || die "$a.tar.gz: unexpected entries: $bad"
  rm -rf "${W:?}/$a"
  tar -xzf "$DIST/$a.tar.gz" -C "$W" --no-same-owner
  (cd "$W" && find "$a" -type f | sort)
done
C=$W/$cli
P=$W/$lib
(cd "$C/bin" && sha256sum --strict -c "$DIST/SHA256SUMS.binaries")

# A small document and the requests sent to it; replies are checked by fields, not by texts.
DOC='{"figram":1,"bot":{"initial":"a","children":{"a":{"on":{"btn:go":[{"to":"b"}]}},"b":{}}}}'
REQS=('{"id":1,"op":"hello"}' '{"id":2,"op":"start"}'
      '{"id":3,"op":"step","snapshot":{"at":"a","seq":1},"event":"btn:go"}' '{"id":4,"op":"no_such_op"}')
WANT=('"protocol":2' '"snapshot":{"at":"a","seq":1}' '"snapshot":{"at":"b","seq":2}' '"code":"bad_request"')
run() { env -i PATH=/usr/bin:/bin "$@"; }

echo "== CLI archive: figram and figram-engine"
T=$W/cli-test; rm -rf "$T"; mkdir -p "$T"
printf '%s\n' "$DOC" > "$T/tiny.bot.json"
run "$C/bin/figram" check "$T/tiny.bot.json" > "$T/check.out" || die "figram check on a valid document"
grep -qx ok "$T/check.out" || die "figram check printed: $(head -3 "$T/check.out")"
n=0
for d in "$C"/examples/*.bot.json; do
  run "$C/bin/figram" check "$d" > /dev/null || die "figram check $(basename "$d")"
  n=$((n + 1))
done
[ $n -gt 0 ] || die "no examples/*.bot.json in $cli"
printf '%s\n' "${REQS[@]}" | run "$C/bin/figram-engine" "$T/tiny.bot.json" > "$T/engine.out" \
  || die "figram-engine exited with an error"
[ "$(wc -l < "$T/engine.out")" -eq ${#REQS[@]} ] || die "figram-engine: expected ${#REQS[@]} replies: $(cat "$T/engine.out")"
for i in "${!REQS[@]}"; do
  sed -n "$((i + 1))p" "$T/engine.out" | grep -qF "${WANT[$i]}" || die "figram-engine reply $((i + 1)) lacks ${WANT[$i]}"
done
for b in figram figram-engine; do
  if ldd "$C/bin/$b" | grep -q 'not found'; then die "$b: missing shared libraries: $(ldd "$C/bin/$b")"; fi
done
echo "ok: figram check (small document and $n examples), figram-engine (${#REQS[@]} requests)"

echo "== library archive: C"
T=$W/c-test; rm -rf "$T"; mkdir -p "$T"
cstr=$(printf '%s' "$DOC" | sed 's/["\\]/\\&/g')
{
  echo '#include <stdio.h>'
  echo '#include <string.h>'
  echo '#include "figram.h"'
  echo "static const char* DOC = \"$cstr\";"
  echo 'static const char* REQS[] = {'
  for r in "${REQS[@]}"; do echo "  \"$(printf '%s' "$r" | sed 's/["\\]/\\&/g')\","; done
  echo '};'
  echo 'static const char* WANT[] = {'
  for w in "${WANT[@]}"; do echo "  \"$(printf '%s' "$w" | sed 's/["\\]/\\&/g')\","; done
  echo '};'
  cat <<'SRC'
int main(void) {
  int rc = figram_init();
  if (rc != 0) { fprintf(stderr, "figram_init = %d: %s\n", rc, figram_last_error()); return 1; }
  char* chk = figram_check_json(DOC);
  if (chk == NULL || strcmp(chk, "ok") != 0) { fprintf(stderr, "check: %s\n", chk ? chk : "NULL"); return 1; }
  figram_free(chk);
  char* err = NULL;
  void* m = figram_load_doc_ex(DOC, &err);
  if (m == NULL) { fprintf(stderr, "load: %s\n", err ? err : "NULL"); return 1; }
  for (size_t i = 0; i < sizeof REQS / sizeof REQS[0]; i++) {
    char* r = figram_request_json(m, REQS[i]);
    if (r == NULL || strstr(r, WANT[i]) == NULL) {
      fprintf(stderr, "request %s: reply %s lacks %s\n", REQS[i], r ? r : "NULL", WANT[i]);
      return 1;
    }
    puts(r);
    figram_free(r);
  }
  figram_unload(m);
  puts("C consumer: ok");
  return 0;
}
SRC
} > "$T/app.c"
CC=${CC:-cc}
command -v pkg-config >/dev/null || die "pkg-config is not installed"
export PKG_CONFIG_PATH=$P/lib/pkgconfig
unset PKG_CONFIG_LIBDIR PKG_CONFIG_SYSROOT_DIR
[ "$(pkg-config --modversion figram)" = "$V" ] || die "pkg-config: figram version $(pkg-config --modversion figram), expected $V"
read -r -a pc <<< "$(pkg-config --cflags --libs figram)"
for t in "${pc[@]}"; do
  case "$t" in -I*|-L*) case "${t:2}" in "$P"/*) ;; *) die "pkg-config flag $t points outside the archive";; esac;; esac
done
read -r -a ld <<< "$(cat "$P/share/figram/LDFLAGS")"
for t in "${ld[@]}"; do case "$t" in -I*|-L*|/*) die "share/figram/LDFLAGS contains a path: $t";; esac; done
(cd "$T" && $CC -std=c99 -Wall -Wextra -Werror -o app-pc app.c "${pc[@]}" -Wl,--gc-sections)
(cd "$T" && $CC -std=c99 -Wall -Wextra -Werror -o app-ldflags app.c -I"$P/include" -L"$P/lib" "${ld[@]}")
for app in app-pc app-ldflags; do
  if ldd "$T/$app" | grep -q 'not found'; then die "$app: missing shared libraries"; fi
  run "$T/$app" > "$T/$app.out" || { cat "$T/$app.out"; die "$app failed"; }
  tail -1 "$T/$app.out"
done
echo "shared libraries of the C program: $(ldd "$T/app-pc" | awk '{print $1}' | tr '\n' ' ')"

echo "== library archive: Go (cgo)"
command -v go >/dev/null || die "go is not installed"
gosrc() {  # gosrc EXTRA_PREAMBLE_LINE
  cat <<SRC
package main

/*
$1
#include <stdlib.h>
#include "figram.h"
*/
import "C"

import (
	"fmt"
	"os"
	"strings"
	"unsafe"
)

const doc = \`$DOC\`

var reqs = []string{$(for r in "${REQS[@]}"; do printf '`%s`, ' "$r"; done)}
var want = []string{$(for w in "${WANT[@]}"; do printf '`%s`, ' "$w"; done)}

func fail(format string, a ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
	os.Exit(1)
}

func main() {
	if rc := C.figram_init(); rc != 0 {
		fail("figram_init = %d: %s", rc, C.GoString(C.figram_last_error()))
	}
	cdoc := C.CString(doc)
	defer C.free(unsafe.Pointer(cdoc))
	m := C.figram_load_doc(cdoc)
	if m == nil {
		fail("load: %s", C.GoString(C.figram_last_error()))
	}
	for i, req := range reqs {
		creq := C.CString(req)
		r := C.figram_request_json(m, creq)
		C.free(unsafe.Pointer(creq))
		if r == nil {
			fail("request %s: NULL reply", req)
		}
		got := C.GoString(r)
		C.figram_free(r)
		if !strings.Contains(got, want[i]) {
			fail("request %s: reply %s lacks %s", req, got, want[i])
		}
		fmt.Println(got)
	}
	C.figram_unload(m)
	fmt.Println("Go consumer: ok")
}
SRC
}
export GOTOOLCHAIN=local GOFLAGS= CGO_ENABLED=1 GO111MODULE=on
for variant in ldflags pkg-config; do
  T=$W/go-test-$variant; rm -rf "$T"; mkdir -p "$T"
  printf 'module figrambundletest\n\ngo 1.23\n' > "$T/go.mod"
  if [ $variant = ldflags ]; then
    gosrc "" > "$T/main.go"
    (cd "$T" && CGO_CFLAGS="-I$P/include" CGO_LDFLAGS="-L$P/lib ${ld[*]}" \
       go build -ldflags=-extldflags=-Wl,--gc-sections -o app .)
  else
    gosrc "#cgo pkg-config: figram" > "$T/main.go"
    (cd "$T" && env -u CGO_CFLAGS -u CGO_LDFLAGS go build -ldflags=-extldflags=-Wl,--gc-sections -o app .)
  fi
  if ldd "$T/app" | grep -q 'not found'; then die "Go program ($variant): missing shared libraries"; fi
  run "$T/app" > "$T/app.out" || { cat "$T/app.out"; die "Go program ($variant) failed"; }
  echo "$(tail -1 "$T/app.out") ($variant)"
done

echo
echo "BUNDLE OK: $cli.tar.gz and $lib.tar.gz are self-contained"
