"""Fault-injecting proxy between the host and a REAL figram-engine process.

    python3 evil_proxy.py ENGINE DOC

Forwards request lines to the engine and response lines back. A control line
{"__evil__": MODE} (never forwarded) arms a fault for the NEXT response:
  corrupt   - response replaced by invalid JSON
  wrong_id  - response id changed
  truncate  - half of the response is written, then the proxy exits (EOF mid-line)
  hang      - no response at all (host must time out)
  garbage   - a non-JSON line is written before the real response
  bad_snapshot - snapshot in the response gets an unknown field
  bad_action   - an action gets an unknown form
"""
import json
import subprocess
import sys
import time


def main():
    eng = subprocess.Popen(sys.argv[1:3], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    out = sys.stdout.buffer
    armed = None
    for line in sys.stdin.buffer:
        if line.startswith(b'{"__evil__"'):
            armed = json.loads(line)["__evil__"]
            continue
        eng.stdin.write(line)
        eng.stdin.flush()
        resp = eng.stdout.readline()
        if not resp:
            sys.exit(1)
        mode, armed = armed, None
        if mode == "corrupt":
            resp = b'{"id":' + resp[6:20].replace(b'"', b"") + b"<<garbage\n"
        elif mode == "wrong_id":
            r = json.loads(resp)
            r["id"] = (r.get("id") or 0) + 1000
            resp = json.dumps(r, ensure_ascii=False).encode() + b"\n"
        elif mode == "truncate":
            out.write(resp[: len(resp) // 2])
            out.flush()
            eng.kill()
            sys.exit(1)
        elif mode == "hang":
            time.sleep(3600)
        elif mode == "garbage":
            out.write(b"!!not json!!\n")
        elif mode == "bad_snapshot":
            r = json.loads(resp)
            if "snapshot" in r:
                r["snapshot"]["evil"] = 1
            resp = json.dumps(r, ensure_ascii=False).encode() + b"\n"
        elif mode == "bad_action":
            r = json.loads(resp)
            if "actions" in r:
                r["actions"].append({"launch": "missiles"})
            resp = json.dumps(r, ensure_ascii=False).encode() + b"\n"
        out.write(resp)
        out.flush()
    eng.stdin.close()
    eng.wait()


if __name__ == "__main__":
    main()
