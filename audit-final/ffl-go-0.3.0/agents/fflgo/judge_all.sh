#!/bin/bash
# judge_all.sh CORPUS OUTPREFIX [MODDIR]: verdicts of ffl-go (MODDIR, default mod), Go ref, Python.
set -e
source /home/user/figram-audit/env.sh
O=/home/user/figram-audit/agents7/fflgo
C=$1; P=$2; M=${3:-$O/mod}
( cd $M && AUDIT_JUDGE_IN=$C AUDIT_JUDGE_OUT=$P.fflgo.tsv go test -count=1 -run '^TestAuditJudge$' . >/dev/null )
$O/refgo/refjudge < $C > $P.refgo.tsv
python3 -I $O/py/pyjudge.py < $C > $P.py.tsv
wc -l $P.fflgo.tsv $P.refgo.tsv $P.py.tsv
