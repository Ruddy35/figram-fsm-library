#!/usr/bin/env python3
"""Proposed additional mutants of ffl-go, run against the module's own tests (go test -count=1 ./),
the same criterion as scripts/mutants.py. The module (commit 51c2312, from the worktree, without
.git and without any audit file) is copied to a temporary directory per mutant; lib/ is hard-linked
(never written). Control: the unmutated copy must pass (exit 0).

    python3 -I my_mutants.py
"""
import os, shutil, subprocess, sys, tempfile

SRC = "/home/user/figram-audit-worktrees/a7-fflgo"
P = "protocol.go"
muts = [
    ("(control) no change", P, "", ""),
    ("trailing text after a reply accepted", P, "\tif dec.More() {\n\t\treturn nil, errors.New(\"text after the JSON value\")",
     "\tif false {\n\t\treturn nil, errors.New(\"text after the JSON value\")"),
    ("snapshot timers not checked", P, "\t\t\tif k == \"\" || !natOK(v) {", "\t\t\tif k == \"\" {"),
    ("snapshot resume values not checked", P, "\t\t\tif k == \"\" || !nameOK(v) {", "\t\t\tif k == \"\" {"),
    ("snapshot version not checked", P, "if v, ok := s[\"v\"]; ok && (!natOK(v) ||", "if v, ok := s[\"v\"]; ok && false && (!natOK(v) ||"),
    ("snapshot at not checked", P, "\tif !nameOK(s[\"at\"]) {", "\tif false {"),
    ("snapshot seq not checked", P, "\tif q, ok := s[\"seq\"]; ok && !natOK(q) {", "\tif q, ok := s[\"seq\"]; ok && false && !natOK(q) {"),
    ("hello states not checked", P, "!natOK(rest[\"protocol\"]) || !natOK(rest[\"states\"])", "!natOK(rest[\"protocol\"])"),
    ("need entries not checked", P, "\t\tfor _, n := range need {\n\t\t\tif !nameOK(n) {", "\t\tfor _, n := range need {\n\t\t\tif false && !nameOK(n) {"),
    ("start_timer token not checked", P, "!natOK(m[\"ms\"]) || !natOK(m[\"token\"])", "!natOK(m[\"ms\"])"),
    ("pending order reversed", P,
     "\t\t\ts.Pending = append(s.Pending, Wait{Call: wm[\"call\"].(string), Token: toUint(wm[\"token\"])})",
     "\t\t\ts.Pending = append([]Wait{{Call: wm[\"call\"].(string), Token: toUint(wm[\"token\"])}}, s.Pending...)"),
    ("timer guards dropped", P,
     "\tf := map[string]any{\"snapshot\": s, \"event\": \"timer:\" + timer, \"token\": token}\n\tif len(guards) > 0 {\n\t\tf[\"guards\"] = guards\n\t}",
     "\tf := map[string]any{\"snapshot\": s, \"event\": \"timer:\" + timer, \"token\": token}\n\t_ = guards"),
    ("outcome refuses an action name with ':'", P, "\tif action == \"\" || outcome == \"\" || strings.Contains(outcome, \":\") {",
     "\tif action == \"\" || outcome == \"\" || strings.Contains(outcome+action, \":\") {"),
    ("snapshot loses v", P, "\t\ts.V = toInt(v)", "\t\t_ = v"),
    ("Decide loop guard removed", P, "\t\t\tif _, ok := known[c]; ok {", "\t\t\tif _, ok := known[c]; ok && false {"),
    ("action detached written for every call", P, "}{Name: a.Name, Detached: a.Detached}", "}{Name: a.Name, Detached: true}"),
    ("hello protocol read as states", P, "return Hello{Protocol: toInt(r[\"protocol\"]), States: toInt(r[\"states\"])}, nil",
     "return Hello{Protocol: toInt(r[\"protocol\"]), States: toInt(r[\"protocol\"])}, nil"),
    ("SetMaxRequest ignores its argument", "figram.go", "\treturn int(C.figram_set_max_request(C.size_t(n)))",
     "\treturn int(C.figram_set_max_request(C.size_t(1 << 20)))"),
    ("timers dropped by toSnapshot", P, "\t\t\ts.Timers[k] = toUint(v)", "\t\t\t_, _ = k, v"),
    ("need duplicates rejected (stricter, as the references)", P, "\t\tfor _, n := range need {\n\t\t\tif !nameOK(n) {",
     "\t\tseen := map[any]bool{}\n\t\tfor _, n := range need {\n\t\t\tif seen[n] {\n\t\t\t\treturn \"\", fmt.Errorf(\"need: duplicate %v\", n)\n\t\t\t}\n\t\t\tseen[n] = true\n\t\t\tif !nameOK(n) {"),
]


def copy_module(dst):
    def ign(d, names):
        return [n for n in names if n in (".git",)]
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel = os.path.relpath(root, SRC)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            s, t = os.path.join(root, f), os.path.join(dst, rel, f)
            if rel.startswith("lib"):
                os.link(s, t)
            else:
                shutil.copy2(s, t)


bad = 0
base = tempfile.mkdtemp()
for name, f, old, new in muts:
    work = os.path.join(base, "ffl-go")
    if os.path.exists(work):
        shutil.rmtree(work)
    copy_module(work)
    p = os.path.join(work, f)
    if old:
        s = open(p).read()
        assert s.count(old) == 1, name
        os.unlink(p)  # never write through a link
        open(p, "w").write(s.replace(old, new))
    r = subprocess.run(["go", "test", "-count=1", "./"], cwd=work, capture_output=True, text=True, timeout=600)
    fails = [l.split()[2] for l in r.stdout.splitlines() if l.startswith("--- FAIL")]
    build = "build failed" in r.stdout + r.stderr or "[setup failed]" in r.stdout
    if not old:
        print(f"{'PASS   ' if r.returncode == 0 else 'BROKEN '} {name}: rc={r.returncode}")
        bad += r.returncode != 0
        continue
    status = "killed  " if r.returncode != 0 and not build else ("BUILD   " if build else "SURVIVED")
    print(f"{status} {name}: rc={r.returncode} {', '.join(fails)[:160]}")
    if build:
        print((r.stdout + r.stderr)[-400:])
shutil.rmtree(base)
print("done")
