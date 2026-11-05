#!/usr/bin/env python3
"""Deliberate defects of the wrapper: each must make `go test` fail.

    python3 scripts/mutants.py        (from any directory; copies the module to a temporary directory)

Exit status 1 if a mutant survives, that is, the tests did not notice the defect.
"""
import os, shutil, subprocess, sys, tempfile
src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = os.path.join(tempfile.mkdtemp(), "ffl-go")
muts = [
 ("snapshot loses resume", "protocol.go", "\t\ts.Resume = make(map[string]string, len(r))\n\t\tfor k, v := range r {\n\t\t\ts.Resume[k] = v.(string)\n\t\t}", "\t\t_ = r"),
 ("Decide drops collected guards", "protocol.go", "\t\tr, err := step(known)", "\t\tr, err := step(guards)"),
 ("reply id not checked", "protocol.go", "func idIs(v any, id int64) bool {\n\treturn natOK(v) &&", "func idIs(v any, id int64) bool {\n\treturn true ||"),
 ("timer token not sent", "protocol.go", '"event": "timer:" + timer, "token": token}', '"event": "timer:" + timer, "token": 1}'),
 ("start ignores seq", "protocol.go", '\t\tf["seq"] = seq', '\t\t_ = f'),
 ("recovered dropped", "protocol.go", 'Recovered: r["recovered"] == true}', 'Recovered: false}'),
 ("action order reversed", "protocol.go", "\t\tres.Actions = append(res.Actions, toAction(a.(map[string]any)))", "\t\tres.Actions = append([]Action{toAction(a.(map[string]any))}, res.Actions...)"),
 ("check ok ignored", "figram.go", '\tif out == "ok" {\n\t\treturn nil\n\t}', '\tif true {\n\t\treturn nil\n\t}'),
 ("SIGPIPE ignored after init", "figram.go", "\tinitOnce.Do(func() {", "\tdefer signal.Ignore(syscall.SIGPIPE)\n\tinitOnce.Do(func() {"),
 ("close does not stop requests", "figram.go", "\tif b.m == nil {\n\t\treturn nil, ErrClosed\n\t}", "\tif b.m == nil {\n\t\treturn []byte(`{}`), nil\n\t}"),
 ("start with an event sends no event", "protocol.go", '\tf["event"] = event\n\tif len(guards) > 0 {\n\t\tf["guards"] = guards\n\t}\n\treturn b.result("start", f)', '\t_ = event\n\tif len(guards) > 0 {\n\t\tf["guards"] = guards\n\t}\n\treturn b.result("start", f)'),
 ("start with an event drops the guards", "protocol.go", '\tf["event"] = event\n\tif len(guards) > 0 {\n\t\tf["guards"] = guards\n\t}', '\tf["event"] = event\n\t_ = guards'),
 ("need of start rejected", "protocol.go", '(op == "step" || op == "start") && keysAre(rest, "need")', 'op == "step" && keysAre(rest, "need")'),
 ("snapshot loses back", "protocol.go", '\tif v, ok := m["back"]; ok {\n\t\ts.Back = v.(string)\n\t}', ''),
 ("empty back accepted", "protocol.go", 'if v, ok := s["back"]; ok && !nameOK(v) {', 'if v, ok := s["back"]; ok && false && !nameOK(v) {'),
 ("protocol version not checked", "figram.go", "\tif p != ProtocolVersion {", "\tif false {"),
 ("outcome token not sent", "protocol.go", '"event": "call:" + action + ":" + outcome, "token": token}', '"event": "call:" + action + ":" + outcome, "token": 1}'),
 ("snapshot loses pending", "protocol.go", "\t\t\ts.Pending = append(s.Pending, Wait{Call: wm[\"call\"].(string), Token: toUint(wm[\"token\"])})", "\t\t\t_ = wm"),
 ("call loses its token", "protocol.go", "\t\t\ta.Outcomes, a.Token = true, toUint(t)", "\t\t\ta.Outcomes = t != nil"),
 ("call loses detached", "protocol.go", 'Detached: m["detached"] == true}', 'Detached: false}'),
 ("pending not checked", "protocol.go", '\t\t\tif !ok || !keysAre(m, "call", "token") || !nameOK(m["call"]) || !natOK(m["token"]) {', '\t\t\tif !ok || m == nil {'),
 ("detached false accepted", "protocol.go", '\t\tif d, ok := m["detached"]; ok && d != true {', '\t\tif d, ok := m["detached"]; ok && d == nil {'),
 ("snapshot_too_large unknown", "protocol.go", "\tCodeSnapshotTooLarge: true,\n", ""),
]
bad = 0
for name, f, old, new in muts:
    if os.path.exists(work): shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns('.git'))
    p = os.path.join(work, f); s = open(p).read()
    assert s.count(old) == 1, name
    s = s.replace(old, new)
    if "signal.Ignore" in new:
        s = s.replace('import (\n', 'import (\n\t"os/signal"\n\t"syscall"\n', 1)
    open(p, 'w').write(s)
    r = subprocess.run(["go", "test", "-count=1", "./"], cwd=work, capture_output=True, text=True)
    fails = [l for l in r.stdout.splitlines() if l.startswith("--- FAIL")]
    killed = r.returncode != 0 and (fails or "build failed" not in r.stdout + r.stderr)
    print(f"{'killed ' if killed else 'SURVIVED'} {name}: rc={r.returncode} {', '.join(x.split()[2] for x in fails)[:160]}")
    if not fails: print((r.stdout + r.stderr)[-600:])
    bad += not killed
shutil.rmtree(os.path.dirname(work))
print(f"mutants {len(muts)}, survived {bad}")
sys.exit(1 if bad else 0)
