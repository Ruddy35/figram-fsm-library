#!/usr/bin/env bash
# FC-02: the sign of a negative-zero number is lost (-0.0 -> 0.0); Python/JS keep it.
F="${1:-/home/user/figram-audit-worktrees/formats-correctness/bin/figram}"
printf -- '-0.0\n[-0.00,-0e5]\n' | "$F" json
python3 -c 'import json,math; v=json.loads("-0.0"); print("python:", v, "copysign:", math.copysign(1, v))'
