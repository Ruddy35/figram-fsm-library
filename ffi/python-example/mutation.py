"""Мутационное тестирование набора сценариев (F04).

Для каждого документа берёт все мутации движка (`figram-engine --mutants`)
и проверяет, что набор сценариев обнаруживает каждую. Тестовый режим движка
включается переменной окружения FIGRAM_ALLOW_MUTANTS=1 (её ставит сам тест).

    python3 mutation.py ENGINE --golden examples/golden.jsonl DOC=TRACES.jsonl ...
"""

import subprocess
import sys

from conformance import corpus, run
from protocol import default_timeout, mutant_env

MIN_MUTANTS = 1   # на документ; пустой список мутаций — провал, а не 100%


def main():
    engine = sys.argv[1]
    items = corpus(sys.argv[2:])
    merged = {}
    for doc, traces in items:
        merged.setdefault(doc, []).extend(traces)
    killed = survived = 0
    survivors = []
    for doc, traces in merged.items():
        mutants = subprocess.run([engine, "--mutants", doc], capture_output=True, text=True,
                                 check=True, timeout=default_timeout(), env=mutant_env()).stdout.split()
        if len(mutants) < MIN_MUTANTS:
            print(f"{doc}: мутаций {len(mutants)} — меньше минимума {MIN_MUTANTS}")
            sys.exit(1)
        n, base = run(engine, doc, traces)
        if base or n == 0:
            print(f"исходный движок не проходит набор для {doc}: {base[:1]}")
            sys.exit(1)
        for m in mutants:
            _, bad = run(engine, doc, traces, m)
            if bad:
                killed += 1
            else:
                survived += 1
                survivors.append(f"{doc}: {m}")
        print(f"{doc}: мутаций {len(mutants)}")
    total = killed + survived
    print(f"обнаружено {killed} из {total} мутаций ({100 * killed // max(total, 1)}%)")
    for s in survivors:
        print("   выжила:", s)
    sys.exit(0 if survived == 0 and total > 0 else 1)


if __name__ == "__main__":
    main()
