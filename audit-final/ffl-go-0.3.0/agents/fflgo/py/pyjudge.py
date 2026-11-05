"""pyjudge: verdicts of the reference Python check_reply (clients/python/protocol.py).
Input JSONL {"k","op","id","line"} on stdin; output "k<TAB>verdict". The line is decoded as
the Python client decodes a reply: UTF-8, json.loads (protocol.check_reply on the value)."""
import json, sys
sys.path.insert(0, "/home/user/figram-audit-worktrees/cleanup-int/clients/python")
import protocol  # noqa: E402

out = sys.stdout
for raw in sys.stdin:
    c = json.loads(raw)
    try:
        v = json.loads(c["line"].encode("utf-8", "surrogatepass").decode("utf-8"))
        verdict = protocol.check_reply(v, c["id"], c["op"])
    except (ValueError, protocol.ProtocolError):
        verdict = "ERR"
    except Exception as e:  # a crash of the reference validator itself
        verdict = "EXC:" + type(e).__name__
    out.write(f"{c['k']}\t{verdict}\n")
