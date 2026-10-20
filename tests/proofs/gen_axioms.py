#!/usr/bin/env python3
"""Extract every `theorem`/`lemma` declared in Figram/*.lean (namespace-aware)
and generate a Lean file that runs `#print axioms` for each of them.

Usage: gen_axioms.py <repo-root> <out.lean> <out-names.txt>
The list is generated from the SOURCES, independently of verify.sh.
"""
import re, sys, pathlib

root = pathlib.Path(sys.argv[1])
out_lean = pathlib.Path(sys.argv[2])
out_names = pathlib.Path(sys.argv[3])

decl_re = re.compile(r'^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|noncomputable\s+)*(theorem|lemma)\s+([^\s:({\[]+)')
ns_re = re.compile(r'^\s*namespace\s+(\S+)')
sec_re = re.compile(r'^\s*(section|mutual)\b\s*(\S+)?')
end_re = re.compile(r'^\s*end\b\s*(\S+)?\s*$')

names = []
for f in sorted((root / 'Figram').glob('*.lean')):
    stack = []  # entries: ('ns', [parts]) | ('sec', name)
    in_block_comment = 0
    for lineno, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
        # crude block-comment tracking (/- ... -/), enough for these sources
        s = line
        if in_block_comment:
            if '-/' in s:
                in_block_comment = 0
            continue
        if s.lstrip().startswith('/-') and '-/' not in s:
            in_block_comment = 1
            continue
        code = s.split('--')[0]
        m = ns_re.match(code)
        if m:
            stack.append(('ns', m.group(1).split('.')))
            continue
        m = sec_re.match(code)
        if m:
            stack.append(('sec', m.group(2)))
            continue
        m = end_re.match(code)
        if m:
            if not stack:
                raise SystemExit(f'{f}:{lineno}: unbalanced end')
            stack.pop()
            continue
        m = decl_re.match(code)
        if m:
            n = m.group(2)
            if n.startswith('_root_.'):
                full = n[len('_root_.'):]
            else:
                prefix = [p for kind, v in stack if kind == 'ns' for p in v]
                full = '.'.join(prefix + [n])
            names.append((full, f'{f.name}:{lineno}'))

if not names:
    raise SystemExit('no theorems found — refusing to produce an empty list')

out_names.write_text(''.join(f'{n}\t{loc}\n' for n, loc in names))
with out_lean.open('w') as o:
    o.write('import Figram\n\n')
    for n, loc in names:
        o.write(f'#print axioms {n}  -- {loc}\n')
print(f'{len(names)} theorems from {len(set(l.split(":")[0] for _, l in names))} files')
