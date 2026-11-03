"""Engine.outcome's docstring: "otherwise the reply is the unchanged snapshot without actions".
A not-accepted outcome on a snapshot whose message is gone is a recovery (start over)."""
import os, sys
repo = sys.argv[1]
sys.path.insert(0, os.path.join(repo, "clients", "python"))
from figram_client import Engine
e = Engine(os.path.join(repo, ".lake/build/bin/figram-engine"), os.path.join(repo, "examples/pizza.bot.json"))
try:
    snap = {"at": "removed_message", "seq": 6}
    r = e.outcome("u", snap, "create_order", "created", 6)
    print("not accepted, message gone:", r)
    snap = {"at": "placing", "seq": 6}
    r = e.outcome("u", snap, "create_order", "created", 6)
    print("not accepted, message exists:", r)
finally:
    e.close()
