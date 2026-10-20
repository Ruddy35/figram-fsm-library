#!/bin/sh
# repeat.sh N CMD... : run CMD N times, print exit-code histogram; exit 0 iff all runs exit 0
N=$1; shift; ok=0; bad=0; i=0
while [ $i -lt $N ]; do i=$((i+1)); "$@" >/dev/null 2>&1; rc=$?; [ $rc -eq 0 ] && ok=$((ok+1)) || { bad=$((bad+1)); echo "run $i rc=$rc"; }; done
echo "runs=$N exit0=$ok nonzero=$bad"; [ $bad -eq 0 ]
