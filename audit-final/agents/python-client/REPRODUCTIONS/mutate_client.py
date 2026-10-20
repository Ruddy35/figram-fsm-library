"""Мутационная проверка тестов клиента: каждая мутация — правка копии
figram_client.py; для каждой запускаются тесты, печатается, какие покраснели.
Мутант «выжил», если все тесты зелёные.

    python3 mutate_client.py SRC_DIR ENGINE PIZZA FEATURES [TEST ...]

SRC_DIR — каталог с figram_client.py, protocol.py и тестами (копируется во
временный каталог). TEST — имена тестов из SRC_DIR (по умолчанию
client_faults.py client_contract.py). Аргументы тестов: ENGINE и документ
(features для client_contract.py, pizza для остальных).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

src, engine, pizza, features = sys.argv[1:5]
tests = sys.argv[5:] or ["client_faults.py", "client_contract.py"]

MUTANTS = {
    "M01 нет пробуждения ожидающих в _fail": (
        "        for slot in pending.values():\n            slot[0].set()\n",
        "        for slot in pending.values():\n            pass\n"),
    "M02 нет срока ожидания": (
        "if not done.wait(max(0.0, deadline - time.monotonic())):",
        "if not done.wait():"),
    "M03 запись в вызывающем потоке (без писателя)": [
        ('        self._outbox.put(line.encode() + b"\\n")\n',
         '        self._p.stdin.write(line.encode() + b"\\n"); self._p.stdin.flush()\n'),
        ('        self._outbox.put(line + b"\\n")\n',
         '        self._p.stdin.write(line + b"\\n"); self._p.stdin.flush()\n')],
    "M04 _kill не убивает": (
        "            os.killpg(self._p.pid, signal.SIGKILL)\n",
        "            pass\n"),
    "M05 нет фонового wait (зомби)": (
        "        threading.Thread(target=self._p.wait, daemon=True).start()\n",
        ""),
    "M06 нет check_reply": (
        "                    check_reply(response, rid, slot[2])\n",
        "                    pass\n"),
    "M07 нет замены неисправного процесса": (
        "        if not p.alive:\n",
        "        if False:\n"),
    "M08 EOF вывода не обнаруживается": (
        '            self._fail("движок закрыл вывод")\n',
        "            pass\n"),
    "M09 _fail не останавливает писателя (утечка потока)": [
        ("            slot[0].set()\n        self._outbox.put(None)\n",
         "            slot[0].set()\n"),
        ("        if first:\n            self._outbox.put(None)\n",
         "        if first:\n")],
    "M10 писатель не закрывает stdin": (
        "                self._p.stdin.close()\n",
        "                pass\n"),
    "M11 _fail не очищает таблицу ожидающих": (
        "            pending, self._pending = self._pending, {}\n",
        "            pending = dict(self._pending)\n"),
    "M12 не проверяются значения guards": (
        "            if missing or wrong:\n",
        "            if False:\n"),
    "M13 неизвестный id игнорируется": (
        '                if slot is None:\n                    self._fail(f"ответ с неизвестным id {rid}")\n                    return\n',
        "                if slot is None:\n                    continue\n"),
    "M14 нет проверки failure перед регистрацией": (
        "            if self.failure is not None:\n                raise",
        "            if False:\n                raise"),
    "M15 срок от записи, а не от начала вызова (+timeout)": (
        "        deadline = time.monotonic() + self._timeout\n",
        "        deadline = time.monotonic() + 2 * self._timeout\n"),
    # Мутанты исправлений аудита (применимы только к исправленному клиенту).
    "M16 нет _check_wire (id:null снова роняет процесс)": (
        "            _check_wire(request)\n", ""),
    "M17 кодирование после регистрации (утечка _pending)": [
        ("                              allow_nan=False).encode()\n",
         "                              allow_nan=False)\n"),
        ('        line = b\'{"id":\' + str(rid).encode() + (b"," + body[1:] if body != b"{}" else b"}")\n',
         '        line = (\'{"id":\' + str(rid) + ("," + body[1:] if body != "{}" else "}")).encode()\n')],
    "M18 закрытый пул воскрешает процессы": (
        "        if self._closed:\n            raise EngineError(\"клиент закрыт\")\n        i = ",
        "        i = "),
    "M18b нет проверки _closed под замком замены": (
        "                if self._closed:\n                    raise EngineError(\"клиент закрыт\")\n",
        ""),
    "M19 OSError при запуске не оборачивается": (
        "                    except OSError as e:",
        "                    except ZeroDivisionError as e:"),
    "M20 killpg при каждом _fail": (
        "        if first:\n            self._outbox.put(None)\n            self._kill()\n",
        "        self._outbox.put(None)\n        self._kill()\n"),
}

orig = open(os.path.join(src, "figram_client.py")).read()
print(f"исходник: {src}; тесты: {tests}", flush=True)


def run_tests(d):
    out = {}
    for t in tests:
        doc = features if t == "client_contract.py" else pizza
        t0 = time.monotonic()
        try:
            extra = [features] if t == "client_robustness.py" else []
            p = subprocess.run([sys.executable, t, engine, doc, *extra], cwd=d, capture_output=True, timeout=150,
                               start_new_session=True)
            rc = p.returncode
            tail = [l for l in p.stdout.decode(errors="replace").splitlines() if l.startswith("НЕТ")][:2]
        except subprocess.TimeoutExpired:
            rc, tail = "timeout", []
        out[t] = (rc, round(time.monotonic() - t0, 1), tail)
    return out


with tempfile.TemporaryDirectory() as d:
    for f in os.listdir(src):
        if f.endswith(".py"):
            shutil.copy(os.path.join(src, f), d)
    base = run_tests(d)
    print("исходный клиент:", {t: v[0] for t, v in base.items()}, flush=True)
    if any(v[0] != 0 for v in base.values()):
        print("ОТКАЗ: тесты красные на исходном клиенте — мутационный анализ бессмыслен", base)
        sys.exit(2)
    survived = 0
    only = [x for x in os.environ.get("ONLY", "").split(",") if x]   # ONLY=M18,M18b — подмножество
    for name, spec in MUTANTS.items():
        if only and name.split()[0] not in only:
            continue
        pairs = spec if isinstance(spec, list) else [spec]
        mutated = orig
        for old, new in pairs:          # все применимые правки (M17: две сразу)
            if mutated.count(old) == 1:
                mutated = mutated.replace(old, new)
        if mutated == orig:
            print(f"{name}: НЕ ПРИМЕНИМА к этому исходнику")
            continue
        with open(os.path.join(d, "figram_client.py"), "w") as f:
            f.write(mutated)
        res = run_tests(d)
        red = [t for t, v in res.items() if v[0] != 0]
        survived += not red
        print(f"{name}: {'ВЫЖИЛ (все тесты зелёные)' if not red else 'убит: ' + ', '.join(red)}  "
              + "; ".join(f"{t}={v[0]} {v[1]}с {v[2]}" for t, v in res.items()), flush=True)
    with open(os.path.join(d, "figram_client.py"), "w") as f:
        f.write(orig)
print(f"выживших мутантов: {survived} из {len(MUTANTS)}")
