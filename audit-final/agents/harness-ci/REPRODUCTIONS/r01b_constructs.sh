#!/usr/bin/env bash
# R01b: which control constructs used in verify.sh / verify-prebuilt.sh abort under `set -euo pipefail`.
for c in 'false | diff - /dev/null && echo ok' 'cmp /etc/hostname /etc/passwd >/dev/null && echo ok' \
         '(cd /tmp && false && echo x)' 'false | tee /dev/null | tail -1' \
         '(cd /tmp && false) || { echo handled; exit 1; }' '[ 0 = 1 ] && Q=1' \
         'for b in a b; do [ $b = a ] && false && echo; done' 'x=$(false; echo y)'; do
  bash -c "set -euo pipefail; $c >/dev/null 2>&1; echo CONTINUED" >/dev/null 2>&1 && r="continues (NOT fatal)" || r="aborts"
  printf '%-62s %s\n' "$c" "$r"
done
