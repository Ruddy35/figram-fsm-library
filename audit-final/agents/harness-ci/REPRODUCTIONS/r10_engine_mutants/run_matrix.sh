#!/usr/bin/env bash
# R10 driver: kill matrix of engine-level mutants against the EXISTING (baseline) harness.
#   run_matrix.sh <build-dir> <lake-cache-dir> [mutant ...]     (default: none + all mutants)
# Needs lean/lake on PATH (source /home/user/figram-audit/env.sh). Columns (1 = mutant killed):
#   BUILD  full `lake build` fails or prints a warning (proofs / #guard / linter)
#   GC     `figram golden` output != examples/golden.jsonl   (verify.sh ignores this: && bug)
#   CG     conformance.py on stored examples/golden.jsonl
#   CT     conformance.py on fresh traces (figram trace 200 40 7, pizza + features)
#   RD     random_docs.py 100 12;  SF snapshot_fixtures.py;  HS host_sim.py;  LT loadtest.py 64 2
#   CF     client_faults.py;  CC client_contract.py (state() flake fixed, see R02)
#   PE     NEW protocol_edges.py (audit/harness-ci), counted only in new_verify
# If the full build fails, the oracle `figram` is the baseline bin/figram (CI-verified) and the
# engine is built alone (`lake build figram-engine`, which does not compile the proofs).
set -u
H=$(cd "$(dirname "$0")" && pwd)
B=$1; CACHE=$2; shift 2
REPO=/home/user/figram-fsm-library
OUT=$H/../../LOGS/r10; mkdir -p "$OUT" "$B"
[ -d "$B/.lake" ] || cp -a "$CACHE" "$B/.lake"
FIXED_CC=/home/user/figram-audit-worktrees/harness-ci/ffi/python-example/client_contract.py
# PE: new test ffi/python-example/protocol_edges.py from branch audit/harness-ci (run with PYTHONPATH of the baseline harness)
NEW_PE=/home/user/figram-audit-worktrees/harness-ci/ffi/python-example/protocol_edges.py
TSV=$OUT/matrix.tsv
[ -f "$TSV" ] || printf 'mutant\tkind\tBUILD\tGC\tCG\tCT\tRD\tSF\tHS\tLT\tCF\tCC\tPE\tcoverage\told_verify\tnew_verify\n' > "$TSV"
run() { # run COL CMD... -> prints 0/1 (1 = nonzero exit = killed)
  local col=$1; shift
  ( cd "$B" && timeout 600 "$@" ) > "$OUT/$m.$col.log" 2>&1 && echo 0 || echo 1
}
LIST=${*:-none $(python3 "$H/mutants.py" list | cut -f1)}
for m in $LIST; do
  git -C "$REPO" archive audit/baseline | tar -x -C "$B"
  cp "$FIXED_CC" "$B/ffi/python-example/client_contract.py"
  kind=base
  if [ "$m" != none ]; then
    python3 "$H/mutants.py" apply "$m" "$B" || { echo "apply failed: $m"; continue; }
    kind=$(python3 "$H/mutants.py" list | awk -F'\t' -v m="$m" '$1==m{print $2}')
  fi
  ( cd "$B" && timeout 900 lake build ) > "$OUT/$m.build.log" 2>&1; brc=$?
  if [ $brc -ne 0 ] || grep -qi warning "$OUT/$m.build.log"; then BUILD=1; else BUILD=0; fi
  ( cd "$B" && timeout 900 lake build figram-engine ) > "$OUT/$m.ebuild.log" 2>&1 || { echo "engine build failed: $m"; printf '%s\t%s\tENGINE-BUILD-FAILED\n' "$m" "$kind" >> "$TSV"; continue; }
  E=$B/.lake/build/bin/figram-engine
  if [ $brc -eq 0 ]; then F=$B/.lake/build/bin/figram; else F=$B/bin/figram; fi
  T=$(mktemp -d)
  GC=$( ( cd "$B" && "$F" golden "$T/g" >/dev/null && cmp "$T/g/golden.jsonl" examples/golden.jsonl ) > "$OUT/$m.GC.log" 2>&1 && echo 0 || echo 1)
  cov=""
  for d in pizza features; do
    ( cd "$B" && "$F" trace examples/$d.bot.json 200 40 7 > "$T/$d.jsonl" 2> "$T/$d.cov" )
    cov="$cov$(sed 's/покрыто вариантов стрелок: //; s/ из /\//' "$T/$d.cov") "
  done
  P=ffi/python-example
  CG=$(run CG python3 $P/conformance.py "$E" --golden examples/golden.jsonl)
  CT=$(run CT python3 $P/conformance.py "$E" examples/pizza.bot.json="$T/pizza.jsonl" examples/features.bot.json="$T/features.jsonl")
  RD=$(run RD python3 $P/random_docs.py "$F" "$E" 100 12)
  SF=$(run SF python3 $P/snapshot_fixtures.py "$E" examples/features.bot.json snapshot.schema.json)
  HS=$(run HS python3 $P/host_sim.py "$E" examples/pizza.bot.json)
  LT=$(run LT python3 $P/loadtest.py "$E" examples/pizza.bot.json "$T/pizza.jsonl" 64 2)
  CF=$(run CF python3 $P/client_faults.py "$E" examples/pizza.bot.json)
  CC=$(run CC python3 $P/client_contract.py "$E" examples/features.bot.json)
  PE=$(run PE python3 "$NEW_PE" "$E" examples/features.bot.json)
  old=$(( BUILD|CG|CT|RD|SF|HS|LT|CF|CC ))
  covbad=$(echo "$cov" | awk '{for(i=1;i<=NF;i++){split($i,a,"/"); if(a[1]!=a[2]) b=1}} END{print b+0}')
  new=$(( old|GC|covbad|PE ))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$m" "$kind" $BUILD $GC $CG $CT $RD $SF $HS $LT $CF $CC $PE "$cov" \
    "$([ $old = 1 ] && echo killed || echo SURVIVES)" "$([ $new = 1 ] && echo killed || echo SURVIVES)" | tee -a "$TSV"
  rm -rf "$T"
done
