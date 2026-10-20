"""Общие помощники для воспроизведений агента python-client.

* CLIENT_DIR — каталог с figram_client.py/protocol.py (по умолчанию копия
  baseline d69bbaf из ../baseline-client); можно указать исправленную ветку.
* ENGINE — бинарник движка; по умолчанию свежая сборка, если она есть,
  иначе PREBUILT из архива. Каждое воспроизведение печатает, что использовано.
* fake(...) — ПОДСТАВНЫЕ ДВИЖКИ (test doubles), не настоящий figram-engine.
"""
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.abspath(os.environ.get("CLIENT_DIR", os.path.join(HERE, "..", "baseline-client")))
sys.path.insert(0, CLIENT_DIR)

FRESH = "/home/user/figram-audit/fresh/figram-engine"
PREBUILT = "/home/user/figram-audit-worktrees/python-client/bin/figram-engine"
ENGINE = os.environ.get("ENGINE") or (FRESH if os.path.exists(FRESH) else PREBUILT)
DOCS = "/home/user/figram-audit-worktrees/python-client/examples"
PIZZA = os.path.join(DOCS, "pizza.bot.json")
FEATURES = os.path.join(DOCS, "features.bot.json")


def label() -> None:
    kind = "FRESH-local" if ENGINE == FRESH else "bin (CI-verified = official fresh build, LOGS/10-ci-verify-run-39330382243.log)" if ENGINE == PREBUILT else "custom"
    print(f"# engine={ENGINE} [{kind}]  client={CLIENT_DIR}", flush=True)


def fake(tmpdir: str, name: str, body: str, python: bool = False) -> str:
    """Записать подставной движок (test double) и вернуть путь."""
    path = os.path.join(tmpdir, name)
    head = f"#!{sys.executable}\n" if python else "#!/bin/sh\n"
    with open(path, "w") as f:
        f.write(head + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def nfds() -> int:
    return len(os.listdir("/proc/self/fd"))


def children():
    """Прямые потомки этого процесса: [(pid, state)], включая зомби."""
    me = os.getpid()
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                rest = f.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(rest[1]) == me:
            out.append((int(d), rest[0]))
    return out


def rss_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return -1.0


def hwm_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return -1.0
