#!/usr/bin/env bash
# C9 (cross-verify, independent): verify.sh's hash step and golden step are non-fatal.
#
# Runs the FIRST FOUR steps of the project's own verify.sh (build, bin/ hashes,
# axioms, golden), copied verbatim with sed, on a scratch copy of the baseline in
# which bin/SHA256SUMS and examples/golden.jsonl are deliberately tampered.
# The final line of verify.sh ("ВСЁ В ПОРЯДКЕ") is appended right after the golden step.
#   variant A (verbatim lines)  -> expected by the claim: prints ВСЁ В ПОРЯДКЕ, exit 0
#   variant B (same lines, but `&& echo ok` replaced by `|| exit 1`) -> must exit 1 (negative control)
#   variant C (verbatim, untampered copy) -> golden step prints "ok" (positive control)
# Usage: c9_verify_nonfatal.sh SRC_WORKTREE SCRATCH_DIR
set -u
SRC=${1:?worktree}; S=${2:?scratch}
source /home/user/figram-audit/env.sh
rm -rf "$S"; mkdir -p "$S"
mk_copy() {  # mk_copy DIR tamper(0/1)
  local d=$1
  mkdir -p "$d"
  (cd "$SRC" && git archive HEAD) | tar -x -C "$d"
  cp -a /home/user/figram-audit/fresh/lake-cache "$d/.lake"
  if [ "$2" = 1 ]; then
    # flip the first hex digit of each hash
    sed -i -E 's/^(.)/\1/; s/^a/b/; s/^8/9/' "$d/bin/SHA256SUMS"
    # change one character inside the first golden scenario line
    sed -i '1s/"actions"/"actionz"/' "$d/examples/golden.jsonl"
  fi
}
# Prefix of verify.sh up to (not including) the conformance step, verbatim.
prefix() { sed -n '1,/^step "Соответствие движка/p' "$1/verify.sh" | sed '$d'; }
run_variant() {  # run_variant NAME DIR SCRIPT
  local name=$1 d=$2 sc=$3
  (cd "$d" && nice -n 10 timeout 900 bash "$sc") > "$S/$name.out" 2>&1
  local rc=$?
  echo "variant $name: exit_code=$rc"
  grep -nE 'хеши|^ok|differ|ВСЁ В ПОРЯДКЕ|^[<>]|^[0-9]+c[0-9]+' "$S/$name.out" | sed "s/^/  [$name] /"
  return 0
}
mk_copy "$S/tampered" 1
mk_copy "$S/clean" 0
echo "--- tamper diff (SHA256SUMS):"; diff "$SRC/bin/SHA256SUMS" "$S/tampered/bin/SHA256SUMS"
echo "--- tamper diff (golden.jsonl, first 120 chars):"; cmp "$SRC/examples/golden.jsonl" "$S/tampered/examples/golden.jsonl"

{ prefix "$S/tampered"; echo 'echo; echo "ВСЁ В ПОРЯДКЕ"'; } > "$S/tampered/verify_prefix.sh"
{ prefix "$S/tampered" | sed -e 's/| diff - bin\/SHA256SUMS && echo "ok: хеши совпадают побайтно"/| diff - bin\/SHA256SUMS || exit 1/' \
      -e 's/^cmp "$T\/golden\/golden.jsonl" examples\/golden.jsonl && echo "ok"/cmp "$T\/golden\/golden.jsonl" examples\/golden.jsonl || exit 1/';
  echo 'echo; echo "ВСЁ В ПОРЯДКЕ"'; } > "$S/tampered/verify_prefix_fatal.sh"
{ prefix "$S/clean"; echo 'echo; echo "ВСЁ В ПОРЯДКЕ"'; } > "$S/clean/verify_prefix.sh"
echo "--- lines under test (verbatim from verify.sh):"
grep -nE 'diff - bin/SHA256SUMS|^cmp ' "$S/tampered/verify_prefix.sh"
echo "--- negative-control rewrite:"
grep -nE 'diff - bin/SHA256SUMS|^cmp ' "$S/tampered/verify_prefix_fatal.sh"
run_variant A_verbatim_tampered "$S/tampered" verify_prefix.sh
run_variant B_fatal_tampered "$S/tampered" verify_prefix_fatal.sh
run_variant C_verbatim_clean "$S/clean" verify_prefix.sh
