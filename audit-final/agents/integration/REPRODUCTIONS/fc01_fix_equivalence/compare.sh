#!/bin/sh
# Byte comparison of `figram json` output of the two FC-01 fixes (4a38b91 formats-correctness,
# d116a64 proofs) and the baseline on corpus.txt (20 156 numbers: random + exponents -990..-1000 + long fractions).
#   compare.sh FIGRAM_4a38b91 FIGRAM_d116a64 FIGRAM_baseline     exit 0 = identical fixed outputs, all re-readable
D=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
"$1" json < "$D/corpus.txt" > "$T/a"; "$2" json < "$D/corpus.txt" > "$T/b"; "$3" json < "$D/corpus.txt" > "$T/o"
cmp "$T/a" "$T/b" && echo "4a38b91 == d116a64: identical, $(wc -l < "$T/a") lines" || { echo "DIFFERENT"; exit 1; }
echo "lines differing from baseline: $(diff "$T/o" "$T/a" | grep -c '^>')"
sed -n 's/^OK //p' "$T/a" > "$T/re"; n=$("$1" json < "$T/re" | grep -vc '^OK')
echo "fixed outputs re-read by figram: $(wc -l < "$T/re") lines, not OK: $n; longest output line: $(awk '{print length}' "$T/a" | sort -n | tail -1)"
sed -n 's/^OK //p' "$T/o" > "$T/reo"; echo "baseline outputs not re-readable: $("$3" json < "$T/reo" | grep -vc '^OK')"
[ "$n" -eq 0 ]
