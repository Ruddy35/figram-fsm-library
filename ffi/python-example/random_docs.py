"""Соответствие движка эталону на случайных документах.

    python3 random_docs.py FIGRAM ENGINE <документов> <сообщений>
"""

import os
import subprocess
import sys
import tempfile

from conformance import load_jsonl, run
from protocol import default_timeout

figram, engine, count, size = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
if count < 1:
    raise SystemExit("число документов должно быть положительным")
T = default_timeout()
total = bad_docs = 0
with tempfile.TemporaryDirectory() as d:
    for seed in range(count):
        doc = os.path.join(d, f"r{seed}.bot.json")
        traces = os.path.join(d, f"r{seed}.jsonl")
        with open(doc, "w") as f:
            subprocess.run([figram, "random", str(seed), size], stdout=f, check=True, timeout=T)
        with open(traces, "w") as f:
            subprocess.run([figram, "trace", doc, "20", "30", str(seed)], stdout=f,
                           stderr=subprocess.DEVNULL, check=True, timeout=T)
        n, bad = run(engine, doc, load_jsonl(traces))
        total += n
        if bad:
            bad_docs += 1
            print(f"seed {seed}: {bad[0][:300]}")
print(f"документов {count}, запросов {total}, документов с расхождениями {bad_docs}")
sys.exit(1 if bad_docs or total == 0 else 0)
