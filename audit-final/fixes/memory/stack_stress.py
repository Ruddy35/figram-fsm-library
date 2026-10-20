"""Глубина рекурсии на системном стеке (ulimit -s): предельная вложенность разделов и автомат на 2 млн
строк. Для каждого документа: `figram check` и загрузка в движок + start + шаг; ни одного аварийного
завершения (переполнение стека — «Stack overflow detected. Aborting.», код −6)."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[3])
import perf  # audit/core-compiler/perf.py
FIG, ENG, CC = sys.argv[1], sys.argv[2], sys.argv[3]
tmp = tempfile.mkdtemp()
docs = {}
for d in (60, 100, 124, 126, 127):
    docs[f"deep{d}"] = json.dumps(perf.deep(d, leaves=50, hist=True))
gen = subprocess.run([sys.executable, os.path.join(CC, "gen_scale_doc.py"), "--states", "2000", "--branches", "2",
                      "--resume", "1", "--timers", "0", "--global", "1000"], capture_output=True, text=True, check=True)
docs["global2000x1000"] = gen.stdout
bad = 0
for name, text in docs.items():
    path = os.path.join(tmp, name + ".json")
    open(path, "w").write(text)
    c = subprocess.run([FIG, "check", path], capture_output=True, text=True, timeout=600)
    e = subprocess.run([ENG, path], input=b'{"id":1,"op":"hello"}\n{"id":2,"op":"start"}\n', capture_output=True, timeout=600)
    out = e.stdout.decode(errors="replace").splitlines()
    crashed = c.returncode < 0 or e.returncode < 0 or b"Stack overflow" in e.stderr or "Stack overflow" in c.stderr
    loaded = e.returncode == 0 and len(out) == 2
    # Документ сверх предела формата (вложенность > 126) должен отвергаться ошибкой, а не падением.
    verdict = "НЕТ" if crashed or (not loaded and c.returncode == 0) else "ok "
    bad += verdict == "НЕТ"
    print(f"{verdict} {name}: {len(text) >> 10} КБ; check код {c.returncode}"
          f"{' (' + c.stdout.strip()[:80] + c.stderr.strip()[:80] + ')' if c.returncode else ''}; "
          f"движок код {e.returncode}, ответов {len(out)}{'; stderr ' + e.stderr.decode(errors='replace')[:120] if e.returncode else ''}",
          flush=True)
print(f"документов {len(docs)}, аварийных завершений {bad}")
sys.exit(1 if bad else 0)
