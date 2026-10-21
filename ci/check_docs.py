#!/usr/bin/env python3
"""Check the documentation against the repository (standard library only).

    python3 ci/check_docs.py [--repo DIR] [--bin DIR] [--no-run]

Exit status 0 when the documentation agrees with the code, 1 when it does not (every problem is
listed), 2 on a usage error. The checks:

  paths      every relative link and every repository path mentioned in README.md and docs/*.md
             exists (generated outputs such as .lake/ and ffi/out/ are exempt);
  theorems   the pinned theorems listed in docs/proofs.md equal tests/proofs/THEOREMS.expected,
             and every theorem name mentioned in the documentation exists in the Lean sources;
  codes      the error codes in docs/format.md equal the codes the engine produces
             (ErrCode.name), the C shim's codes are among them and are listed in docs/embedding.md,
             the protocol validators of the Python and Go clients know exactly these codes; every
             request field of every operation (opKeys) is documented;
  cli        the commands of the README CLI table equal the usage text of Main.lean, the engine
             usage line equals EngineMain.lean, and the documented exit statuses equal the ones
             the sources return;
  capi       the functions in the docs/embedding.md table equal the functions declared in
             ffi/figram.h, which equal the functions exported by ffi/figram_ffi.c;
  numbers    numbers that the documentation states about the tests equal their sources;
  cyrillic   no Cyrillic text in the Markdown files at the root and in docs/;
  examples   runnable examples run and print what the documentation shows (skipped with --no-run):
               ```sh doctest      `$ ` starts a command, `> ` continues it, other lines are the
                                  expected stdout; `...` in a line matches any text, a line `...`
                                  matches any number of lines, a last line `[exit N]` expects that
                                  exit status (default 0); the commands of one block run in one
                                  bash process from the repository root;
               ```c doctest args=ARGS    compiled with `cc -I ffi $(cat ffi/out/LDFLAGS)` and run
                                  with ARGS; expected stdout in the next ```text output block;
               ```python doctest  run with python3 (clients/python on PYTHONPATH); expected stdout
                                  in the next ```text output block.
             `figram` and `figram-engine` are taken from --bin (default .lake/build/bin), put first
             on PATH.

Until the repository layout is final, a few sources are also looked up at their former locations
(ci/THEOREMS.expected, ffi/python-example, ffi/go-example, audit/proofs); the paths check always
uses the documented paths.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

ap = argparse.ArgumentParser(description="Check the documentation against the repository.")
ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                help="repository root (default: the parent of this script's directory)")
ap.add_argument("--bin", default=None, help="directory with figram and figram-engine (default: REPO/.lake/build/bin)")
ap.add_argument("--no-run", action="store_true", help="do not run the examples")
args = ap.parse_args()
ROOT = os.path.abspath(args.repo)
BIN = os.path.abspath(args.bin) if args.bin else os.path.join(ROOT, ".lake", "build", "bin")

problems = {}       # check name -> list of messages
notes = []          # informational lines


def fail(check, msg):
    problems.setdefault(check, []).append(msg)


def rel(p):
    return os.path.relpath(p, ROOT)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def exists(path):
    return os.path.exists(os.path.join(ROOT, path))


def first_existing(*paths):
    """The first of the candidate paths that exists; a note when it is not the first one."""
    for p in paths:
        if exists(p):
            if p != paths[0]:
                notes.append(f"{paths[0]} not found, using the former location {p}")
            return p
    return None


def flat(text):
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------- Markdown model

FENCE = re.compile(r"^(```+|~~~+)\s*(.*)$")


class Doc:
    """A Markdown file split into prose lines (with numbers) and fenced blocks."""

    def __init__(self, path):
        self.path = path
        self.text = read(path)
        self.prose = []      # (line number, text) outside fenced blocks
        self.blocks = []     # dicts: info, lines, start (line number of the opening fence)
        fence = None
        for n, line in enumerate(self.text.split("\n"), 1):
            m = FENCE.match(line)
            if fence is None:
                if m:
                    fence = {"marker": m.group(1)[0] * 3, "info": m.group(2).strip(), "lines": [], "start": n}
                else:
                    self.prose.append((n, line))
            elif line.strip().startswith(fence["marker"]) and line.strip().strip(fence["marker"][0]) == "":
                self.blocks.append(fence)
                fence = None
            else:
                fence["lines"].append(line)
        if fence is not None:
            fail("paths", f"{path}:{fence['start']}: unterminated code block")

    def prose_text(self):
        return "\n".join(t for _, t in self.prose)

    def section(self, heading):
        """Prose and block text from a heading (exact title, any level) to the next heading of the
        same or a higher level."""
        lines = self.text.split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"^(#+)\s+(.*?)\s*$", line)
            if m and m.group(2) == heading:
                level = len(m.group(1))
                out = []
                for l2 in lines[i + 1:]:
                    m2 = re.match(r"^(#+)\s", l2)
                    if m2 and len(m2.group(1)) <= level:
                        break
                    out.append(l2)
                return "\n".join(out)
        return None


DOC_PATHS = ["README.md"] + sorted(rel(p) for p in glob.glob(os.path.join(ROOT, "docs", "*.md")))
ROOT_MD = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.md")))
DOCS = {p: Doc(p) for p in sorted(set(DOC_PATHS) | set(ROOT_MD))}


def doc(path):
    if path not in DOCS:
        fail("paths", f"{path}: missing")
        return None
    return DOCS[path]


# ---------------------------------------------------------------- paths

TOP = ("Figram/", "ffi/", "clients/", "schemas/", "examples/", "docs/", "tests/", "ci/", ".github/")
EXTS = (".lean", ".py", ".sh", ".c", ".h", ".cpp", ".go", ".json", ".jsonl", ".md", ".yml", ".yaml",
        ".txt", ".expected", ".sha256", ".supp", ".mod")
GENERATED = (".lake/", "ffi/out", "verify-logs", "tests/capi/out", "clients/go/zz_figram_ldflags.go",
             "clients/go/gofig", "zz_figram_ldflags.go")
_names = None


def repo_basenames():
    global _names
    if _names is None:
        _names = set()
        for d, subdirs, files in os.walk(ROOT):
            subdirs[:] = [s for s in subdirs if s not in (".git", ".lake", "out", "__pycache__", "verify-logs")]
            _names.update(files)
    return _names


def check_path(where, token):
    token = token.rstrip(".,;:)")
    if token.startswith("./"):
        token = token[2:]
    if not token or token.startswith(GENERATED) or os.path.basename(token.rstrip("/")) in GENERATED:
        return
    if "*" in token:
        if not glob.glob(os.path.join(ROOT, token)):
            fail("paths", f"{where}: `{token}` matches no file")
        return
    if "/" in token:
        if not exists(token):
            fail("paths", f"{where}: `{token}` does not exist")
    elif token not in repo_basenames() and not exists(token):
        fail("paths", f"{where}: `{token}` is not a file of the repository")


def path_like(token):
    if token.startswith("./"):
        token = token[2:]
    if not re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_./*+-]*", token) or token.startswith("."):
        return token.startswith(".github/")
    if token.startswith(TOP):
        return True
    return "/" not in token and token.endswith(EXTS) and not token.startswith("x-")


def check_paths():
    for path in DOC_PATHS:
        d = DOCS[path]
        base = os.path.dirname(path)
        for n, line in d.prose:
            where = f"{path}:{n}"
            for target in re.findall(r"\]\(([^)\s]+)\)", line):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                file_part = target.split("#", 1)[0]
                p = os.path.normpath(os.path.join(base, file_part))
                if not exists(p):
                    fail("paths", f"{where}: link target {target} does not exist")
            for code in re.findall(r"`([^`]+)`", line):
                if path_like(code):
                    check_path(where, code)
        for b in d.blocks:
            if b["info"].split()[:1] not in (["sh"], ["text"], ["bash"], []):
                continue
            for k, line in enumerate(b["lines"], b["start"] + 1):
                for tok in re.split(r"[\s'\"=()|;&<>]+", line):
                    if tok.startswith(TOP) and "$" not in tok:
                        check_path(f"{path}:{k}", tok)


# ---------------------------------------------------------------- Lean sources and theorems

LEAN_FILES = None


def lean_sources():
    global LEAN_FILES
    if LEAN_FILES is None:
        LEAN_FILES = {}
        pats = ["*.lean", "Figram/*.lean", "tests/proofs/**/*.lean", "audit/proofs/**/*.lean"]
        for pat in pats:
            for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
                LEAN_FILES[rel(p)] = open(p, encoding="utf-8").read()
    return LEAN_FILES


def lean_declares(name):
    short = name.split(".")[-1]
    rx = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)?(?:(?:private|protected|noncomputable|partial)\s+)*"
                    r"(theorem|lemma|def|abbrev|structure|inductive|class)\s+(?:[\w.]+\.)?" + re.escape(short) + r"\b",
                    re.M)
    return any(rx.search(src) for src in lean_sources().values())


def check_theorems():
    exp_path = first_existing("tests/proofs/THEOREMS.expected", "ci/THEOREMS.expected")
    if exp_path is None:
        fail("theorems", "tests/proofs/THEOREMS.expected not found")
        return
    pinned = re.findall(r"^STMT (\S+) :", read(exp_path), re.M)
    if not pinned:
        fail("theorems", f"{exp_path}: no STMT lines")
    d = doc("docs/proofs.md")
    if d is None:
        return
    sec = d.section("Pinned theorems")
    if sec is None:
        fail("theorems", "docs/proofs.md: no section 'Pinned theorems'")
        return
    sec = sec.split("\n### How the hypotheses")[0]
    listed = re.findall(r"^\| `(Figram\.[\w.]+)` \|", sec, re.M)
    for t in sorted(set(pinned) - set(listed)):
        fail("theorems", f"pinned theorem {t} ({exp_path}) is not listed in docs/proofs.md")
    for t in sorted(set(listed) - set(pinned)):
        fail("theorems", f"docs/proofs.md lists {t} as pinned, but it is not in {exp_path}")
    dup = sorted({t for t in listed if listed.count(t) > 1})
    if dup:
        fail("theorems", f"docs/proofs.md lists twice: {dup}")
    # every qualified Figram name, and every name of a "Theorem" column or a *Proved:* note, exists
    for path in DOC_PATHS + [p for p in ROOT_MD if p not in DOC_PATHS]:
        dd = DOCS[path]
        for n, line in dd.prose:
            names = re.findall(r"`(Figram\.[A-Z]\w*(?:\.\w+)+)`", line)
            names += re.findall(r"\*Proved:\*\s*`(\w+)`", line)
            for nm in names:
                if not lean_declares(nm):
                    fail("theorems", f"{path}:{n}: `{nm}` is not declared in the Lean sources")
        table = re.search(r"^\| Property \| Theorem \|\n\|[-| ]+\|\n((?:\|.*\n)+)", dd.text, re.M)
        if table:
            for row in table.group(1).splitlines():
                for nm in re.findall(r"`(\w+)`", row.split("|")[-2]):
                    if not lean_declares(nm):
                        fail("theorems", f"{path}: theorem `{nm}` (table 'Property | Theorem') is not declared")
    notes.append(f"pinned theorems: {len(pinned)} in {exp_path}, {len(listed)} listed in docs/proofs.md")


# ---------------------------------------------------------------- error codes and request fields

def check_codes():
    eng = read("Figram/Engine.lean")
    m = re.search(r"def ErrCode\.name.*?(?=\n\S)", eng, re.S)
    engine_codes = set(re.findall(r'\|\s*\.\w+\s*=>\s*"(\w+)"', m.group(0))) if m else set()
    if not engine_codes:
        fail("codes", "Figram/Engine.lean: no codes found in ErrCode.name")
        return
    d = doc("docs/format.md")
    if d is None:
        return
    sec = d.section("Errors") or ""
    table = re.search(r"^\| `code` \| When \|\n\|[-| ]+\|\n((?:\|.*\n)+)", sec + "\n", re.M)
    documented = set(re.findall(r"^\| `(\w+)` \|", table.group(1), re.M)) if table else set()
    if not documented:
        fail("codes", "docs/format.md: no error code table ('| `code` | When |') in section 'Errors'")
    for c in sorted(engine_codes - documented):
        fail("codes", f"the engine produces `{c}` (ErrCode.name), docs/format.md does not document it")
    for c in sorted(documented - engine_codes):
        fail("codes", f"docs/format.md documents `{c}`, the engine does not produce it")
    shim = set(re.findall(r'err_reply\("(\w+)"', read("ffi/figram_ffi.c")))
    for c in sorted(shim - engine_codes):
        fail("codes", f"ffi/figram_ffi.c produces `{c}`, which is not an engine code")
    emb = doc("docs/embedding.md")
    if emb is not None:
        para = re.search(r"\*\*Request errors detected by the shim\*\*(.*?)\n\n(.*?)\n\n", emb.text, re.S)
        listed = set(re.findall(r"^\* `(\w+)` —", para.group(2), re.M)) if para else set()
        if listed != shim:
            fail("codes", f"docs/embedding.md lists shim codes {sorted(listed)}, ffi/figram_ffi.c produces {sorted(shim)}")
    proto = first_existing("clients/python/protocol.py", "ffi/python-example/protocol.py")
    if proto:
        pm = re.search(r"ERROR_CODES = frozenset\(\{([^}]*)\}\)", read(proto))
        py = set(re.findall(r'"(\w+)"', pm.group(1))) if pm else set()
        if py != engine_codes:
            fail("codes", f"{proto} ERROR_CODES {sorted(py)} != engine codes {sorted(engine_codes)}")
    gomain = first_existing("clients/go/main.go", "ffi/go-example/main.go")
    if gomain:
        gm = re.search(r"var errorCodes = map\[string\]bool\{(.*?)\n\}", read(gomain), re.S)
        go = set(re.findall(r'"(\w+)": true', gm.group(1))) if gm else set()
        if go != engine_codes:
            fail("codes", f"{gomain} errorCodes {sorted(go)} != engine codes {sorted(engine_codes)}")
    # request fields: every field of opKeys is named in the strict-request paragraph
    km = re.search(r"def opKeys.*?(?=\n/--|\ndef )", eng, re.S)
    ops = dict(re.findall(r'\|\s*"(\w+)"\s*=>\s*some \[([^\]]*)\]', km.group(0))) if km else {}
    if not ops:
        fail("codes", "Figram/Engine.lean: no operations found in opKeys")
    fmt = flat(d.text)
    para = re.search(r"Requests are strict: (.*?)\.\s", fmt)
    para = para.group(1) if para else ""
    if not para:
        fail("codes", "docs/format.md: no paragraph 'Requests are strict: …'")
    for op, keys in ops.items():
        if f'"op": "{op}"' not in fmt:
            fail("codes", f"docs/format.md: operation `{op}` is not in the request table")
        # "(`hello` — `id`, `op`; `start` — also `seq`; …)": each operation names its own fields
        seg = re.search(rf"`{op}` — ([^;)]*)", para)
        fields = re.findall(r'"(\w+)"', keys)
        own = [k for k in fields if k not in ("id", "op")] or fields
        for k in own:
            if not seg or f"`{k}`" not in seg.group(1):
                fail("codes", f"docs/format.md: field `{k}` of `{op}` is not named for `{op}` in 'Requests are strict'")
    documented_ops = set(re.findall(r'`\{"op": "(\w+)"', fmt))
    for op in sorted(documented_ops - set(ops)):
        fail("codes", f"docs/format.md documents operation `{op}`, opKeys does not know it")
    lim = re.search(r"def defaultMaxRequest : Nat := (\d+)", eng)
    if lim:
        n = int(lim.group(1))
        spaced = f"{n:,}".replace(",", " ")
        if spaced not in fmt:
            fail("codes", f"docs/format.md: the default request limit {spaced} bytes is not stated")
        shim_lim = re.search(r"g_max_request = (\d+);", read("ffi/figram_ffi.c"))
        if not shim_lim or int(shim_lim.group(1)) != n:
            fail("codes", f"ffi/figram_ffi.c default request limit differs from defaultMaxRequest {n}")
        if spaced not in flat(emb.text if emb else ""):
            fail("codes", f"docs/embedding.md: the default request limit {spaced} bytes is not stated")
    for phrase in ("--max-request", "FIGRAM_ALLOW_MUTANTS=1", '"mutant"'):
        if phrase not in fmt:
            fail("codes", f"docs/format.md does not mention {phrase}")
    notes.append(f"error codes: {sorted(engine_codes)}; shim: {sorted(shim)}; operations: {sorted(ops)}")


# ---------------------------------------------------------------- CLI

def usage_lines(src, name):
    m = re.search(r"def usage : String :=\n(.*?)\n\n", src, re.S)
    if not m:
        return []
    text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))).replace("\\n", "\n")
    return [l.strip() for l in text.split("\n") if l.strip()]


def check_cli():
    main = read("Main.lean")
    lines = usage_lines(main, "figram")
    if not lines or not lines[0].startswith("usage: figram "):
        fail("cli", "Main.lean: usage text not found")
        return
    body = " ".join(lines)[len("usage: figram "):]
    alts = [re.sub(r"\s+", " ", a.strip()) for a in body.split("|")]
    usage_cmds = {a.split()[0]: a for a in alts}
    readme = doc("README.md")
    if readme is None:
        return
    sec = readme.section("CLI: `figram`") or ""
    rows = re.findall(r"^\| `figram ([^`]+)` \|", sec, re.M)
    doc_cmds = {r.split()[0]: re.sub(r"\s+", " ", r) for r in rows}
    if not rows:
        fail("cli", "README.md: no command table in section 'CLI: `figram`'")
    for c in sorted(set(usage_cmds) - set(doc_cmds)):
        fail("cli", f"Main.lean usage has `figram {usage_cmds[c]}`, the README table does not")
    for c in sorted(set(doc_cmds) - set(usage_cmds)):
        fail("cli", f"README documents `figram {doc_cmds[c]}`, Main.lean usage does not have it")
    for c in sorted(set(doc_cmds) & set(usage_cmds)):
        if doc_cmds[c] != usage_cmds[c]:
            fail("cli", f"README `figram {doc_cmds[c]}` != Main.lean usage `figram {usage_cmds[c]}`")
    handled = set(re.findall(r'^\s*\| \["(\w+)"', main, re.M))
    for c in sorted(handled - set(usage_cmds)):
        if f"`figram {c}`" not in sec:
            fail("cli", f"Main.lean handles the hidden command `{c}`, README does not mention `figram {c}`")
    for c in sorted(set(usage_cmds) - handled):
        fail("cli", f"Main.lean usage lists `{c}`, but `run` does not handle it")
    # exit statuses
    m_ret = {int(x) for x in re.findall(r"return (\d+)", main[main.find("def main"):])}
    sent = re.search(r"`figram` exits with status ([^.]*)\.", flat(sec))
    doc_ret = {int(x) for x in re.findall(r"\b(\d+)\b", sent.group(1))} if sent else set()
    if m_ret != doc_ret:
        fail("cli", f"README: `figram` exit statuses {sorted(doc_ret)} != Main.lean {sorted(m_ret)}")
    eng = read("EngineMain.lean")
    elines = usage_lines(eng, "figram-engine")
    esec = readme.section("Engine: `figram-engine`") or ""
    eblock = re.search(r"```text\n(.*?)\n```", esec, re.S)
    if not elines or not eblock or "usage: " + eblock.group(1).strip() != elines[0]:
        fail("cli", f"README engine usage {eblock.group(1).strip() if eblock else None!r} != EngineMain.lean {elines[:1]}")
    e_ret = {int(x) for x in re.findall(r"return (\d+)", eng[eng.find("def main"):])}
    esent = re.search(r"It exits with status (.*?)arguments\.", flat(esec))
    e_doc = {int(x) for x in re.findall(r"\b([0-9])\b", esent.group(1))} if esent else set()
    if e_ret != e_doc:
        fail("cli", f"README: figram-engine exit statuses {sorted(e_doc)} != EngineMain.lean {sorted(e_ret)}")
    notes.append(f"CLI commands: {sorted(usage_cmds)}; hidden: {sorted(handled - set(usage_cmds))}")


# ---------------------------------------------------------------- C ABI

def check_capi():
    h = set(re.findall(r"FIGRAM_EXPORT [a-z_ *]+\b(figram_[a-z_]+)\(", read("ffi/figram.h")))
    c = set(re.findall(r"^FIGRAM_API [a-z_ *]+\b(figram_[a-z_]+)\(", read("ffi/figram_ffi.c"), re.M))
    if not h:
        fail("capi", "ffi/figram.h: no FIGRAM_EXPORT functions found")
    if h != c:
        fail("capi", f"ffi/figram.h declares {sorted(h - c)} not exported by ffi/figram_ffi.c; "
                     f"exported but not declared: {sorted(c - h)}")
    d = doc("docs/embedding.md")
    if d is None:
        return
    sec = d.section("The C ABI contract") or ""
    table = re.search(r"^\| Function \| Purpose \|\n\|[-| ]+\|\n((?:\|.*\n)+)", sec, re.M)
    listed = set()
    if table:
        for row in table.group(1).splitlines():
            listed.update(re.findall(r"`(figram_\w+)`", row.split("|")[1]))
    else:
        fail("capi", "docs/embedding.md: no '| Function | Purpose |' table")
    if listed != h:
        fail("capi", f"docs/embedding.md table: missing {sorted(h - listed)}, unknown {sorted(listed - h)}")
    for n, line in d.prose:
        for f in re.findall(r"`(figram_[a-z_]+)(?:\(|`)", line):
            if f not in h and f not in ("figram_shutdown",):
                fail("capi", f"docs/embedding.md:{n}: `{f}` is not a function of ffi/figram.h")
    if "figram_shutdown" in h:
        fail("capi", "ffi/figram.h declares figram_shutdown, docs/embedding.md says there is none")
    # topics of the contract that the page must keep (formerly ffi/audit/doc_check.sh)
    text = flat(d.text)
    for topic in ("SIGPIPE", "sigaltstack", "io_uring", "RLIMIT_AS", "mimalloc", "R_X86_64_TPOFF32",
                  "one handle per thread", "Undefined behaviour", "do not see the Lean heap",
                  "lean_mark_mt", "--gc-sections"):
        if topic not in text:
            fail("capi", f"docs/embedding.md no longer mentions {topic!r}")
    ffi = read("ffi/figram_ffi.c")
    if "lean_mark_mt(" not in ffi or "lean_mark_persistent" in ffi + read("Figram/Api.lean"):
        fail("capi", "docs/embedding.md says loaded machines are marked with lean_mark_mt; ffi/figram_ffi.c disagrees")
    if "-Wl,--gc-sections" not in read("ffi/build.sh"):
        fail("capi", "ffi/build.sh no longer passes -Wl,--gc-sections, docs/embedding.md says LDFLAGS contains it")
    notes.append(f"C ABI functions: {len(h)}")


# ---------------------------------------------------------------- numbers stated about the tests

def count_lines(path, rx=r"\S"):
    return sum(1 for l in read(path).splitlines() if re.search(rx, l))


def after(text, anchor, rx):
    """First match of rx after the first occurrence of anchor in text."""
    i = text.find(anchor)
    if i < 0:
        return None
    m = re.search(rx, text[i:])
    return int(m.group(1)) if m else None


def dict_entries(src, name):
    m = re.search(rf"^(\s*){name} = \{{\n(.*?)^\1\}}", src, re.S | re.M)
    return len(re.findall(r'^\s+"[^"]+":', m.group(2), re.M)) if m else None


def check_numbers():
    exp = first_existing("tests/proofs/THEOREMS.expected", "ci/THEOREMS.expected")
    txt = first_existing("tests/proofs/theorems.txt", "audit/proofs/theorems.txt")
    engine_dir = first_existing("tests/engine", "ffi/python-example") or "tests/engine"
    v = read("verify.sh")
    faults = read(f"{engine_dir}/client_faults.py") if exists(f"{engine_dir}/client_faults.py") else ""
    robust = read(f"{engine_dir}/client_robustness.py") if exists(f"{engine_dir}/client_robustness.py") else ""
    host = read(f"{engine_dir}/host_sim.py") if exists(f"{engine_dir}/host_sim.py") else ""
    m_mut = re.search(r"^MIN_MUTANTS=(\d+)", v, re.M)
    m_self = re.search(r"^MIN_SELFTEST_OK=(\d+)", v, re.M)
    rd = re.search(r"random_docs\.py \S+ \S+ (\d+) (\d+)", v)
    rdsrc = read(f"{engine_dir}/random_docs.py") if exists(f"{engine_dir}/random_docs.py") else ""
    rdtrace = re.search(r'"trace", doc, "(\d+)", "(\d+)"', rdsrc)
    tr = re.search(r"trace examples/\$d\.bot\.json (\d+) (\d+) \d+", v)
    fz = re.search(r"json_fuzz\.py \$F (\d+)", v)
    robust_max = max((int(x) for x in re.findall(r"^R(\d+)\s", robust, re.M)), default=None)
    users = re.search(r"def scenario_orders\([^)]*users=(\d+)", host)
    pool = re.search(r"ThreadPoolExecutor\((\d+)\) as pool:\n\s+list\(pool\.map\(user_stream", host)
    threads8 = re.search(r"for i in range\((\d+)\)\]", faults)
    blocked = re.search(r"threading\.Thread\(target=small\) for _ in range\((\d+)\)", faults)
    truth = {
        "pinned": count_lines(exp, r"^STMT ") if exp else None,
        "source theorems": count_lines(txt) if txt else None,
        "golden": count_lines("examples/golden.jsonl"),
        "mutants": int(m_mut.group(1)) if m_mut else None,
        "selftest": int(m_self.group(1)) if m_self else None,
        "snapshots": after(v, "snapshot_fixtures.py", r"g\[0\] >= (\d+)"),
        "edges": after(v, "protocol_edges.py", r"g\[0\] >= (\d+)"),
        "capi": after(v, "regress.sh", r"g\[0\] >= (\d+)"),
        "random docs": int(rd.group(1)) if rd else None,
        "random messages": int(rd.group(2)) if rd else None,
        "random traces": int(rdtrace.group(1)) if rdtrace else None,
        "random length": int(rdtrace.group(2)) if rdtrace else None,
        "trace count": int(tr.group(1)) if tr else None,
        "trace length": int(tr.group(2)) if tr else None,
        "fuzz": int(fz.group(1)) if fz else None,
        "request faults": dict_entries(faults, "FAKES"),
        "start faults": dict_entries(faults, "START"),
        "fault threads": int(threads8.group(1)) if threads8 else None,
        "blocked requests": int(blocked.group(1)) if blocked else None,
        "robustness": robust_max,
        "users": int(users.group(1)) if users else None,
        "host threads": int(pool.group(1)) if pool else None,
    }
    # (document, regular expression on whitespace-collapsed text, quantity)
    claims = [
        ("README.md", r"(\d+) pinned theorems", "pinned"),
        ("CHANGELOG.md", r"(\d+) theorem statements are pinned", "pinned"),
        ("docs/proofs.md", r"The statements of these (\d+) theorems are pinned", "pinned"),
        ("docs/proofs.md", r"\((\d+) theorems\)", "source theorems"),
        ("docs/testing.md", r"statements of the (\d+) pinned theorems", "pinned"),
        ("docs/testing.md", r"axioms of all (\d+) source theorems", "source theorems"),
        ("docs/testing.md", r"golden\.jsonl` \((\d+) scenarios\)", "golden"),
        ("docs/testing.md", r"generates (\d+) random reference scenarios", "trace count"),
        ("docs/testing.md", r"random reference scenarios of length (\d+)", "trace length"),
        ("docs/testing.md", r"`tests/engine/random_docs\.py`: (\d+) random", "random docs"),
        ("docs/testing.md", r"with (\d+) messages each", "random messages"),
        ("docs/testing.md", r"checked on (\d+) reference scenarios", "random traces"),
        ("docs/testing.md", r"reference scenarios of length (\d+)\.", "random length"),
        ("docs/testing.md", r"at least (\d+) on the two example documents", "mutants"),
        ("docs/testing.md", r"\(at least (\d+) snapshots", "snapshots"),
        ("docs/testing.md", r"\(at least (\d+) hand-written cases", "edges"),
        ("docs/testing.md", r"`tests/capi/regress\.sh`, at least (\d+) checks", "capi"),
        ("docs/testing.md", r"tests fail \(at least (\d+) checks\)", "selftest"),
        ("docs/testing.md", r"`tests/engine/json_fuzz\.py`: ([\d ]+) random inputs", "fuzz"),
        ("docs/host-contract.md", r"`tests/engine/client_faults\.py` — (\d+) kinds of engine failure", "request faults"),
        ("docs/host-contract.md", r"request \((\d+) threads each\)", "fault threads"),
        ("docs/host-contract.md", r"threads each\), (\d+) before readiness", "start faults"),
        ("docs/host-contract.md", r"and (\d+) concurrent requests to a process that does not read", "blocked requests"),
        ("docs/host-contract.md", r"\(checks R1–R(\d+):", "robustness"),
        ("docs/host-contract.md", r"models production: (\d+) users", "users"),
        ("docs/host-contract.md", r"delivered twice and concurrently, (\d+) threads", "host threads"),
    ]
    for path, rx, what in claims:
        d = DOCS.get(path)
        if d is None:
            fail("numbers", f"{path}: missing")
            continue
        m = re.search(rx, flat(d.text))
        if not m:
            fail("numbers", f"{path}: statement /{rx}/ not found (update the documentation or this check)")
            continue
        stated = int(m.group(1).replace(" ", ""))
        if truth[what] is None:
            fail("numbers", f"{path}: cannot determine '{what}' from the sources")
        elif stated != truth[what]:
            fail("numbers", f"{path}: states {stated} for '{what}', the sources say {truth[what]}")
    notes.append("numbers: " + ", ".join(f"{k}={v}" for k, v in truth.items()))


# ---------------------------------------------------------------- Cyrillic

def check_cyrillic():
    ranges = [(0x0400, 0x052F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F)]   # Cyrillic blocks
    rx = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in ranges) + "]")
    for path in sorted(set(DOC_PATHS) | set(ROOT_MD)):
        for n, line in enumerate(DOCS[path].text.split("\n"), 1):
            if rx.search(line):
                fail("cyrillic", f"{path}:{n}: {line.strip()[:80]}")


# ---------------------------------------------------------------- runnable examples

def wild_match(expected, actual):
    """expected lines with `...` wildcards (a line `...` = any number of lines) against actual lines."""
    def line_ok(e, a):
        if "..." not in e:
            return e.rstrip() == a.rstrip()
        rx = ".*".join(re.escape(p) for p in e.rstrip().split("..."))
        return re.fullmatch(rx, a.rstrip()) is not None

    memo = {}

    def go(i, j):
        if (i, j) in memo:
            return memo[(i, j)]
        if i == len(expected):
            r = j == len(actual)
        elif expected[i].strip() == "...":
            r = any(go(i + 1, k) for k in range(j, len(actual) + 1))
        else:
            r = j < len(actual) and line_ok(expected[i], actual[j]) and go(i + 1, j + 1)
        memo[(i, j)] = r
        return r

    return go(0, 0)


def run_env():
    env = dict(os.environ)
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "")
    env["LC_ALL"] = "C.UTF-8"
    py = first_existing("clients/python", "ffi/python-example")
    if py:
        env["PYTHONPATH"] = os.path.join(ROOT, py) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.pop("FIGRAM_ALLOW_MUTANTS", None)
    return env


def report_run(where, what, expected, out, err, rc=None):
    msg = [f"{where}: {what}", "    expected:"] + ["      " + l for l in expected] + ["    got:"] + \
          ["      " + l for l in out.split("\n")[-40:]]
    if rc is not None:
        msg.append(f"    exit status {rc}")
    if err.strip():
        msg += ["    stderr:"] + ["      " + l for l in err.strip().split("\n")[-10:]]
    fail("examples", "\n".join(msg))


def run_sh(where, lines, tmp):
    cmds = []           # (command text, expected lines, expected status)
    for line in lines:
        if line.startswith("$ "):
            cmds.append([line[2:], [], 0])
        elif line.startswith("> ") and cmds and not cmds[-1][1]:
            cmds[-1][0] += "\n" + line[2:]
        elif cmds:
            cmds[-1][1].append(line)
        elif line.strip():
            fail("examples", f"{where}: output line before the first `$ ` command: {line!r}")
    for c in cmds:
        while c[1] and not c[1][-1].strip():
            c[1].pop()
        if c[1] and re.fullmatch(r"\[exit (\d+)\]", c[1][-1].strip()):
            c[2] = int(re.fullmatch(r"\[exit (\d+)\]", c[1].pop().strip()).group(1))
    script = os.path.join(tmp, "example.sh")
    with open(script, "w", encoding="utf-8") as f:
        f.write("set -o pipefail\n")
        for c in cmds:
            f.write(c[0] + "\nprintf '\\036%d\\036' \"$?\"\n")
    try:
        p = subprocess.run(["bash", "--noprofile", "--norc", script], cwd=ROOT, env=run_env(),
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        fail("examples", f"{where}: timed out")
        return
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    parts = re.split(r"\x1e(\d+)\x1e", out)
    if len(parts) != 2 * len(cmds) + 1:
        report_run(where, "the block did not run to the end", [c[0] for c in cmds], out, err, p.returncode)
        return
    for k, c in enumerate(cmds):
        text, rc = parts[2 * k], int(parts[2 * k + 1])
        if text.endswith("\n"):
            text = text[:-1]
        got = text.split("\n") if text else []
        if rc != c[2] or not wild_match(c[1], got):
            report_run(f"{where}: $ {c[0].splitlines()[0]}", f"output or exit status (want {c[2]}) differs",
                       c[1], text, err, rc)


def expected_output(blocks, i, where):
    if i + 1 < len(blocks) and blocks[i + 1]["info"] == "text output":
        lines = list(blocks[i + 1]["lines"])
        while lines and not lines[-1].strip():
            lines.pop()
        return lines
    fail("examples", f"{where}: a runnable example must be followed by a ```text output block")
    return None


def run_c(where, info, lines, expected, tmp):
    ldflags = os.path.join(ROOT, "ffi", "out", "LDFLAGS")
    if not os.path.exists(ldflags):
        fail("examples", f"{where}: ffi/out/LDFLAGS is missing: run `bash ffi/build.sh` first (or --no-run)")
        return
    cc = os.environ.get("CC", "cc")
    src, exe = os.path.join(tmp, "example.c"), os.path.join(tmp, "example")
    with open(src, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(ldflags, encoding="utf-8") as f:
        flags = f.read().split()
    p = subprocess.run([cc, "-o", exe, src, "-I", os.path.join(ROOT, "ffi")] + flags, cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode != 0:
        fail("examples", f"{where}: does not compile:\n{p.stderr[-2000:]}")
        return
    m = re.search(r"args=(\S+)", info)
    argv = m.group(1).split(",") if m else []
    p = subprocess.run([exe] + argv, cwd=ROOT, env=run_env(), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    got = p.stdout.rstrip("\n").split("\n") if p.stdout else []
    if p.returncode != 0 or not wild_match(expected, got):
        report_run(where, "output or exit status differs", expected, p.stdout, p.stderr, p.returncode)


def run_python(where, lines, expected, tmp):
    src = os.path.join(tmp, "example.py")
    with open(src, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    p = subprocess.run([sys.executable, src], cwd=ROOT, env=run_env(), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=300)
    got = p.stdout.rstrip("\n").split("\n") if p.stdout else []
    if p.returncode != 0 or not wild_match(expected, got):
        report_run(where, "output or exit status differs", expected, p.stdout, p.stderr, p.returncode)


def check_examples():
    missing = [b for b in ("figram", "figram-engine") if not os.access(os.path.join(BIN, b), os.X_OK)]
    if missing:
        fail("examples", f"{', '.join(missing)} not found in {BIN}: build with `lake build` or pass --bin (or --no-run)")
        return
    n = 0
    for path in sorted(set(DOC_PATHS) | set(ROOT_MD)):
        blocks = DOCS[path].blocks
        for i, b in enumerate(blocks):
            kind = b["info"].split()
            if len(kind) < 2 or kind[1] != "doctest":
                continue
            where = f"{path}:{b['start']}"
            tmp = tempfile.mkdtemp(prefix="check_docs.")
            try:
                n += 1
                if kind[0] == "sh":
                    run_sh(where, b["lines"], tmp)
                elif kind[0] == "c":
                    exp = expected_output(blocks, i, where)
                    if exp is not None:
                        run_c(where, b["info"], b["lines"], exp, tmp)
                elif kind[0] == "python":
                    exp = expected_output(blocks, i, where)
                    if exp is not None:
                        run_python(where, b["lines"], exp, tmp)
                else:
                    fail("examples", f"{where}: unknown runnable example kind {kind[0]!r}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    notes.append(f"runnable examples: {n}")


# ---------------------------------------------------------------- main

CHECKS = [("paths", check_paths), ("theorems", check_theorems), ("codes", check_codes), ("cli", check_cli),
          ("capi", check_capi), ("numbers", check_numbers), ("cyrillic", check_cyrillic)]
if not args.no_run:
    CHECKS.append(("examples", check_examples))
for name, fn in CHECKS:
    try:
        fn()
    except (OSError, ValueError, KeyError, IndexError) as e:
        fail(name, f"check crashed: {type(e).__name__}: {e}")
    msgs = problems.get(name, [])
    print(f"{'ok  ' if not msgs else 'FAIL'} {name}" + (f": {len(msgs)} problem(s)" if msgs else ""))
    for m in msgs:
        print("     " + m.replace("\n", "\n     "))
if args.no_run:
    print("skip examples (--no-run)")
for n in notes:
    print("note: " + n)
total = sum(len(v) for v in problems.values())
print(f"check_docs: {total} problem(s)")
sys.exit(1 if total else 0)
