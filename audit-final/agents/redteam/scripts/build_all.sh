#!/usr/bin/env bash
R=/home/user/figram-audit/agents5/redteam
cd $R
for f in DEFECTS/d*.edit.py; do
  n=$(basename $f .edit.py)
  [ -x bin/mut/$n/figram-engine ] && continue
  case $n in d14*|d15*|d27*|d28*) opt=--format;; *) opt=;; esac
  echo "=== $n $opt"
  python3 scripts/defect.py $n $opt 2>&1
done
