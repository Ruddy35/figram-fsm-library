"""Мутационная проверка тестов клиента (fix/client) — адаптация
agents/python-client/REPRODUCTIONS/mutate_client.py к новому figram_client.py.

    python3 mutate_client_v2.py SRC_DIR ENGINE PIZZA FEATURES [TEST ...]
    ONLY=M01,M21  — подмножество;  APPLY_ONLY=1 — только проверить применимость;
    FAST=1 — для мутанта тесты до первого красного; SKIP_BASE=1 — без прогона исходника.

M01–M20 — те же идеи, что у python-client (переписаны под новый код; M17 — то же).
M21+ — мутанты исправлений fix/client.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

src, engine, pizza, features = (os.path.abspath(a) for a in sys.argv[1:5])
tests = sys.argv[5:] or ["client_faults.py", "client_contract.py", "client_robustness.py"]

MUTANTS = {
    "M01 нет пробуждения ожидающих в _fail": (
        "        for c in pending.values():\n            c.done.set()\n",
        "        for c in pending.values():\n            pass\n"),
    "M02 нет срока ожидания": (
        "if not c.done.wait(max(0.0, end - time.monotonic())):",
        "if not c.done.wait():"),
    "M03 запись в вызывающем потоке (без писателя)": (
        '        self._outbox.put((rid, line + b"\\n"))\n',
        '        self._p.stdin.write(line + b"\\n"); self._p.stdin.flush()\n'),
    "M04 _kill не убивает": (
        "            os.killpg(self._p.pid, signal.SIGKILL)\n",
        "            pass\n"),
    "M05 нет фонового wait (зомби)": (
        "        threading.Thread(target=self._p.wait, daemon=True).start()\n", ""),
    "M06 нет check_reply": (
        "                check_reply(resp, rid, c.op)\n",
        "                pass\n"),
    "M07 нет замены неисправного процесса": (
        "        if p.alive:\n            return p\n        wait = spawn_error = None\n",
        "        if True:\n            return p\n        wait = spawn_error = None\n"),
    "M08 EOF вывода не обнаруживается": (
        '        self._fail("движок закрыл вывод")\n',
        "        pass\n"),
    "M09 _fail не останавливает писателя (утечка потока)": (
        "            self._started.set()\n            self._outbox.put(None)\n",
        "            self._started.set()\n"),
    "M10 писатель не закрывает stdin": (
        "                stdin.close()\n",
        "                pass\n"),
    "M11 _fail не очищает таблицу ожидающих": (
        "            pending, self._pending = self._pending, {}\n",
        "            pending = dict(self._pending)\n"),
    "M12 не проверяются значения guards": (
        "            if missing or wrong:\n",
        "            if False:\n"),
    "M13 неизвестный id игнорируется": (
        '        if c is None:\n            self._fail(f"ответ с неизвестным id {rid}")\n            return False\n',
        "        if c is None:\n            return True\n"),
    "M14 нет проверки failure перед регистрацией": (
        "            failed = self.failure is not None\n",
        "            failed = False\n"),
    "M15 срок от записи, а не от начала вызова (+timeout)": (
        "        end = time.monotonic() + limit\n",
        "        end = time.monotonic() + 2 * limit\n"),
    "M16 нет _check_wire (id:null снова роняет процесс)": (
        "            _check_wire(request)\n", ""),
    "M17 кодирование после регистрации (утечка _pending)": [
        ("                              allow_nan=False).encode()\n",
         "                              allow_nan=False)\n"),
        ('        line = b\'{"id":\' + str(rid).encode() + (b"," + body[1:] if body != b"{}" else b"}")\n',
         '        line = (\'{"id":\' + str(rid) + ("," + body[1:] if body != "{}" else "}")).encode()\n')],
    "M18 закрытый пул воскрешает процессы": (
        "        if self._closed:\n            raise EngineError(_CLOSED, retryable=False)\n        i = ",
        "        i = "),
    "M18b нет проверки _closed под замком замены": (
        "            if self._closed:\n                raise EngineError(_CLOSED, retryable=False)\n            p = ",
        "            p = "),
    "M19 OSError при запуске не оборачивается": (
        "                except OSError as e:   # нет бинарника",
        "                except ZeroDivisionError as e:   # нет бинарника"),
    "M20 killpg при каждом _fail": (
        "            if kill:\n                self._kill()\n        return first\n",
        "        if kill:\n            self._kill()\n        return first\n"),
    # --- мутанты исправлений fix/client ---
    "M21 I-01 вызов не ждёт готовности (срок от вызова)": (
        "        self.wait_ready()\n        limit = self._timeout\n",
        "        limit = self._timeout\n"),
    "M22 I-01 нет срока запуска": (
        "            if left <= 0:\n                self._fail(f\"движок не ответил на hello",
        "            if False:\n                self._fail(f\"движок не ответил на hello"),
    "M23 I-01 конструктор не ждёт загрузки": (
        "        if wait_ready:\n", "        if False:\n"),
    "M24 I-04 таймаут убивает процесс (как раньше)": (
        "            if mine:\n                self._probe_health()\n",
        "            if mine:\n                self._fail(\"таймаут\")\n"),
    "M25 I-04 нет обнаружения зависания": (
        "        if left <= 0:\n            self._fail(f\"движок завис",
        "        if False:\n            self._fail(f\"движок завис"),
    "M26 I-04 брошенный id забывается (поздний ответ = чужой id)": (
        "                    if c.sent:\n                        c.abandoned = True       # ответ придёт — отбросить\n"
        "                    else:\n                        del self._pending[rid]   # не записан — писатель пропустит\n",
        "                    del self._pending[rid]\n"),
    "M27 I-03 id:null не относится к старейшему": (
        "            if rid is None and is_error and head is not None:",
        "            if False:"),
    "M28 I-03 порядок ответов не проверяется": (
        "        if rid != head:\n", "        if False:\n"),
    "M29 I-07 нет паузы перезапуска": (
        "        if fails <= 0 or base <= 0:\n            return 0.0\n",
        "        return 0.0\n"),
    "M30 I-07 stderr не сохраняется": [
        ("                            self._add_stderr(chunk)\n", "                            pass\n"),
        ("            self._add_stderr(chunk)\n        for _ in range(5):", "            pass\n        for _ in range(5):")],
    "M31 I-07 пауза не сбрасывается после работы": (
        "                if p.served:\n                    self._fails[i] = 0\n", ""),
    "M32 I-10 нет предела строки без конца строки": (
        "                        if max_line and flen > max_line:", "                        if False:"),
    "M32b I-10 нет предела полной строки": (
        "                        if max_line and len(line) > max_line:", "                        if False:"),
    "M33 I-11 close по очереди": (
        "        for p in procs:\n            p._begin_close()\n        deadline = time.monotonic() + self._cfg.timeout\n"
        "        for p in procs:\n            p._finish_close(deadline)\n",
        "        for p in procs:\n            p.close()\n"),
    "M34 I-12 нет проверки срока шага после guards": (
        "            if time.monotonic() >= deadline:\n                raise EngineError(f\"шаг не уложился",
        "            if False:\n                raise EngineError(f\"шаг не уложился"),
    "M35 I-12 второй запрос шага без общего срока": (
        "            response = proc.call({**request, \"guards\": {n: values[n] for n in need}}, deadline)\n",
        "            response = proc.call({**request, \"guards\": {n: values[n] for n in need}})\n"),
    "M36 I-13 мягкий UTF-8 (surrogatepass)": (
        '    text = raw.decode("utf-8")', '    text = raw.decode("utf-8", "surrogatepass")'),
    "M37 I-13 экранированный суррогат не проверяется": (
        "    if _ESC_SURROGATE.search(text):", "    if False:"),
    "M38 нет code/retryable у ошибки движка": (
        '            raise EngineError(response["error"], code=response.get("code"), retryable=False)\n',
        '            raise EngineError(response["error"])\n'),
    "M39 ответ-ошибка с лишними полями принимается": (
        '    if keys not in ({"error"}, {"error", "code"}):\n', "    if False:\n"),
    "M40 hello не проверяется": (
        '            elif c.op == "hello":\n                _check_hello(resp)\n',
        '            elif c.op == "hello":\n                pass\n'),
    "M41 close ждёт EOF от загружающегося процесса": (
        "        first = self._fail(_CLOSED, kill=loading)\n", "        first = self._fail(_CLOSED, kill=False)\n"),
    "M42 нет проверки живости после таймаута": (
        "            if mine:\n                self._probe_health()\n", "            if mine:\n                pass\n"),
    "M43 served не отмечается": (
        "                self.served = True\n", "                pass\n"),
    "M44 нет кода выхода в ошибке": (
        "        if st.si_code == os.CLD_EXITED:", "        if False:"),
    "M45 срок шага от вызова, а не от готовности": (
        "        proc.wait_ready()\n        deadline = time.monotonic() + self._step_timeout\n",
        "        deadline = time.monotonic() + self._step_timeout\n"),
    "M46 call игнорирует deadline": (
        "        if deadline is not None and deadline < end:", "        if False:"),
    "M47 поздний ответ брошенного вызова доставляется (abandoned не учитывается)": (
        "                if not c.abandoned:\n                    c.response = resp\n",
        "                c.response = resp\n"),
}

orig = open(os.path.join(src, "figram_client.py")).read()
print(f"исходник: {src}; тесты: {tests}; движок: {engine}", flush=True)
bad = []
for name, spec in MUTANTS.items():
    for old, _ in (spec if isinstance(spec, list) else [spec]):
        if orig.count(old) != 1:
            bad.append((name, orig.count(old), old[:70]))
if bad:
    for b in bad:
        print("НЕ ПРИМЕНИМА:", b)
if os.environ.get("APPLY_ONLY"):
    print(f"мутантов {len(MUTANTS)}, неприменимых {len(bad)}")
    sys.exit(1 if bad else 0)


def run_tests(d):
    out = {}
    for t in tests:
        doc = features if t == "client_contract.py" else pizza
        t0 = time.monotonic()
        try:
            extra = [features] if t == "client_robustness.py" else []
            p = subprocess.run([sys.executable, t, engine, doc, *extra], cwd=d, capture_output=True, timeout=240,
                               start_new_session=True)
            rc = p.returncode
            if rc == 0 and p.stderr.strip():
                rc = "stderr"           # verify.sh (clean) требует пустой stderr
            tail = [l for l in p.stdout.decode(errors="replace").splitlines() if l.startswith("НЕТ")][:2]
        except subprocess.TimeoutExpired:
            rc, tail = "timeout", []
        out[t] = (rc, round(time.monotonic() - t0, 1), tail)
        if rc != 0 and os.environ.get("FAST"):
            break               # FAST=1: до первого красного теста
    return out


with tempfile.TemporaryDirectory() as d:
    for f in os.listdir(src):
        if f.endswith(".py"):
            shutil.copy(os.path.join(src, f), d)
    only = [x for x in os.environ.get("ONLY", "").split(",") if x]
    if not os.environ.get("SKIP_BASE"):
        base = run_tests(d)
        print("исходный клиент:", {t: v[0] for t, v in base.items()}, flush=True)
        if any(v[0] != 0 for v in base.values()):
            print("ОТКАЗ: тесты красные на исходном клиенте — мутационный анализ бессмыслен", base)
            sys.exit(2)
    survived = run = 0
    for name, spec in MUTANTS.items():
        if only and name.split()[0] not in only:
            continue
        pairs = spec if isinstance(spec, list) else [spec]
        mutated = orig
        for old, new in pairs:
            if mutated.count(old) == 1:
                mutated = mutated.replace(old, new)
        if mutated == orig:
            print(f"{name}: НЕ ПРИМЕНИМА к этому исходнику")
            continue
        with open(os.path.join(d, "figram_client.py"), "w") as f:
            f.write(mutated)
        res = run_tests(d)
        red = [t for t, v in res.items() if v[0] != 0]
        run += 1
        survived += not red
        print(f"{name}: {'ВЫЖИЛ (все тесты зелёные)' if not red else 'убит: ' + ', '.join(red)}  "
              + "; ".join(f"{t}={v[0]} {v[1]}с {v[2]}" for t, v in res.items()), flush=True)
    with open(os.path.join(d, "figram_client.py"), "w") as f:
        f.write(orig)
print(f"выживших мутантов: {survived} из {run}")
