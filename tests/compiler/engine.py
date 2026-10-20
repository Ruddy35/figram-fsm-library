"""Minimal driver for `figram-engine` (FORMAT.md §7): one JSON line per request."""

import json
import os
import select
import subprocess
import tempfile


class EngineLoadError(Exception):
    pass


class Engine:
    def __init__(self, binary, doc_text, mutate=None, timeout=60.0):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".bot.json", delete=False)
        self.tmp.write(doc_text)
        self.tmp.close()
        args = [binary]
        if mutate:
            args += ["--mutate", mutate]
        args.append(self.tmp.name)
        self.timeout = timeout
        # --mutate is a test mode enabled only by FIGRAM_ALLOW_MUTANTS=1 (fix wave, P-17)
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1,
                                  env=dict(os.environ, FIGRAM_ALLOW_MUTANTS="1") if mutate else None)
        self.n = 0
        hello = self.req({"op": "hello"})
        if hello is None:
            err = self.p.stderr.read()
            self.close()
            raise EngineLoadError(err.strip())
        self.states = hello.get("states")

    def req(self, obj):
        self.n += 1
        obj = dict(obj)
        obj["id"] = self.n
        try:
            self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
        except BrokenPipeError:
            return None
        r, _, _ = select.select([self.p.stdout], [], [], self.timeout)
        if not r:
            raise TimeoutError("engine did not answer")
        line = self.p.stdout.readline()
        if not line:
            return None
        rep = json.loads(line)
        if rep.get("id") != self.n:
            raise AssertionError(f"id mismatch {rep.get('id')} != {self.n}")
        return rep

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass


def engine_loads(binary, doc_text):
    """True if the engine accepts the document (load + check + wf)."""
    try:
        e = Engine(binary, doc_text)
    except EngineLoadError:
        return False
    e.close()
    return True
