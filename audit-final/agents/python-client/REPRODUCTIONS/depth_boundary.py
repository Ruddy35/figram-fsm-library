import sys
sys.path.insert(0, sys.argv[1])
from figram_client import Engine, EngineError
eng = Engine(sys.argv[2], sys.argv[3], timeout=5)
def nested(levels):
    d = 1
    for _ in range(levels):
        d = {"a": d}
    return d
# request object = container 1, snapshot = 2, so x carries total-2 containers
for total in (255, 256, 257):
    snap = {"at": "menu", "x": nested(total - 2)}
    pid = eng._procs[0]._p.pid
    try:
        eng.step("u", snap, "btn:x")
        r = "ответ"
    except EngineError as e:
        r = "EngineError: " + str(e)[:90]
    print(total, "контейнеров:", r, "| процесс тот же:", eng._procs[0]._p.pid == pid and eng._procs[0].alive)
eng.close()
