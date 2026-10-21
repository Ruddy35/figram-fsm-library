"""Shared helpers of the formats suite.

Independent re-implementation (in Python) of the pieces of Figram that the
tests use as an oracle:

* J model: numbers as exact (m, e) pairs taken from the source text,
  objects as ordered lists of pairs, strings as Python str;
* the Lean printer `render` / `renderCompact` (FormatIO.lean) — re-written
  from the specification in docs/format.md / the source comments;
* the document decoder + encoder (docs/format.md, section 4) — `canon_doc`.

Nothing here imports or calls Figram code; the binaries are only run as
subprocesses through `run()`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIGRAM = os.environ.get("FIGRAM", os.path.join(ROOT, ".lake", "build", "bin", "figram"))
ENGINE = os.environ.get("FIGRAM_ENGINE", os.path.join(ROOT, ".lake", "build", "bin", "figram-engine"))
MAX_INT = 2 ** 53 - 1
MAX_EXP = 1000
MAX_DEPTH = 256


def run(args, stdin: bytes | None = None, timeout: float = 20.0):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------
# J model
# --------------------------------------------------------------------------
# null -> None, bool -> True/False, number -> Num(m, e), string -> str,
# array -> list, object -> Obj([(k, v), ...])


class Num(tuple):
    """Exact decimal m * 10^e as produced by the Lean parser from the text."""

    def __new__(cls, m: int, e: int):
        return tuple.__new__(cls, (m, e))

    @property
    def m(self):
        return self[0]

    @property
    def e(self):
        return self[1]

    def value(self) -> Fraction:
        return Fraction(self.m) * (Fraction(10) ** self.e)

    def __repr__(self):
        return f"Num({self.m},{self.e})"


class Obj(list):
    """Ordered list of (key, value) pairs."""

    def get(self, k, default=None):
        for kk, v in self:
            if kk == k:
                return v
        return default

    def has(self, k):
        return any(kk == k for kk, _ in self)

    # an object never equals an array (Obj subclasses list only for convenience)
    def __eq__(self, other):
        return isinstance(other, Obj) and list.__eq__(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = None

    def __repr__(self):
        return "Obj(" + list.__repr__(self) + ")"


NUM_RE = re.compile(r"^(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$")


def num_of_text(t: str) -> Num:
    """Lean `parseNum`: m = ip·10^fc + fp (sign applied, -0 = 0), e = ex − fc."""
    mt = NUM_RE.match(t)
    if not mt:
        raise ValueError(f"not a JSON number: {t!r}")
    neg, ip, fp, ex = mt.groups()
    fp = fp or ""
    m = int(ip + fp)
    if neg:
        m = -m
    e = (int(ex) if ex else 0) - len(fp)
    return Num(m, e)


class Reject(Exception):
    pass


def _pairs(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Reject("duplicate key")
    return Obj(pairs)


def _no_const(x):
    raise Reject(f"constant {x}")


def _exp_text_ok(t: str) -> bool:
    mt = NUM_RE.match(t)
    return mt.group(4) is None or abs(int(mt.group(4))) <= MAX_EXP


def _num_hook(t: str):
    if not _exp_text_ok(t):
        raise Reject("exponent > 1000")
    return num_of_text(t)


def _walk(v, depth=0):
    if isinstance(v, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise Reject("lone surrogate")
    elif isinstance(v, Obj):
        if depth + 1 > MAX_DEPTH:
            raise Reject("depth")
        for k, x in v:
            _walk(k, depth + 1)
            _walk(x, depth + 1)
    elif isinstance(v, list):
        if depth + 1 > MAX_DEPTH:
            raise Reject("depth")
        for x in v:
            _walk(x, depth + 1)


def load_j(text: str, deviations: bool = True):
    """Python json + the documented Figram deviations (docs/format.md, section 3, "Numbers",
    FormatIO.lean header): duplicate keys, |exponent| > 1000, depth > 256,
    lone surrogates, NaN/Infinity are rejected.  Raises Reject/ValueError."""
    if deviations:
        v = json.loads(text, object_pairs_hook=_pairs, parse_int=_num_hook,
                       parse_float=_num_hook, parse_constant=_no_const)
        _walk(v)
        return v
    # "lax" oracle — plain RFC 8259 / Python semantics (last duplicate wins etc.)
    return json.loads(text, object_pairs_hook=lambda p: Obj(dict(p).items()),
                      parse_int=num_of_text, parse_float=num_of_text)


# --------------------------------------------------------------------------
# Printer — re-implementation of FormatIO.render / renderCompact
# --------------------------------------------------------------------------

def escape(s: str) -> str:
    out = []
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20:
            out.append("\\u00%02x" % ord(c))
        else:
            out.append(c)
    return "".join(out)


def render_num(m: int, e: int) -> str:
    """FormatIO.render for numbers, with the FC-01 rule applied: scientific
    notation only while |e| <= maxExponent (otherwise the parser could not
    read the text back); smaller e are written positionally.  On the baseline
    binary this differs only for e < -1000 (finding FC-01)."""
    if e == 0:
        return str(m)
    if e > 0 or -MAX_EXP <= e < -30:
        return f"{m}e{e}"
    neg = m < 0
    ds = str(abs(m))
    k = -e
    if len(ds) <= k:
        ds = "0" * (k + 1 - len(ds)) + ds
    return ("-" if neg else "") + ds[: len(ds) - k] + "." + ds[len(ds) - k:]


def render(v, ind: str = "") -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, Num):
        return render_num(v.m, v.e)
    if isinstance(v, str):
        return '"' + escape(v) + '"'
    if isinstance(v, Obj):
        if not v:
            return "{}"
        ind2 = ind + "  "
        return "{\n" + ",\n".join(ind2 + '"' + escape(k) + '": ' + render(x, ind2) for k, x in v) \
            + "\n" + ind + "}"
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(isinstance(x, str) for x in v):
            return "[" + ", ".join(render(x, ind) for x in v) + "]"
        ind2 = ind + "  "
        return "[\n" + ",\n".join(ind2 + render(x, ind2) for x in v) + "\n" + ind + "]"
    raise TypeError(v)


def compact(v) -> str:
    if isinstance(v, Obj):
        return "{" + ",".join('"' + escape(k) + '":' + compact(x) for k, x in v) + "}"
    if isinstance(v, list):
        return "[" + ",".join(compact(x) for x in v) + "]"
    return render(v)


# --------------------------------------------------------------------------
# Writer with full control over the text (for non-canonical inputs)
# --------------------------------------------------------------------------

class RawNum:
    """A number written with an exact chosen text (e.g. '7.2e3')."""

    def __init__(self, text: str):
        num_of_text(text)
        self.text = text


def write(v, ws=lambda: "", ascii_=False) -> str:
    """Serialise J/RawNum with arbitrary inter-token whitespace."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, RawNum):
        return v.text
    if isinstance(v, Num):
        return render_num(v.m, v.e)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=ascii_)
    if isinstance(v, Obj):
        items = [ws() + json.dumps(k, ensure_ascii=ascii_) + ws() + ":" + ws() + write(x, ws, ascii_) + ws()
                 for k, x in v]
        return "{" + (",".join(items) if items else ws()) + "}"
    if isinstance(v, list):
        items = [ws() + write(x, ws, ascii_) + ws() for x in v]
        return "[" + (",".join(items) if items else ws()) + "]"
    raise TypeError(v)


def to_j(py):
    """Plain Python JSON value (dict/list/int/str) -> J model (int only)."""
    if isinstance(py, dict):
        return Obj([(k, to_j(v)) for k, v in py.items()])
    if isinstance(py, list):
        return [to_j(x) for x in py]
    if isinstance(py, bool) or py is None or isinstance(py, str):
        return py
    if isinstance(py, int):
        return Num(py, 0)
    if isinstance(py, (Num, RawNum, Obj)):
        return py
    raise TypeError(py)


# --------------------------------------------------------------------------
# Document decoder + encoder oracle (docs/format.md: section 4, C1 of section 2, decisions 11/12 of section 3)
# --------------------------------------------------------------------------

DOC_KEYS = ["figram", "requires", "conditions", "calls", "variables", "bot"]
NODE_KEYS = ["initial", "children", "on", "timers"]
VARIANT_KEYS = ["if", "to", "resume", "do"]
KNOWN_FEATURES: list[str] = []


class DecodeError(Exception):
    pass


def num_to_nat(n):
    """docs/format.md, decision 12: value of the number if it is a non-negative
    integer.  Mirrors the documented shortcut 'huge powers are not computed'
    (e > 18 with m > 0 is rejected: such a number exceeds 2^53 anyway)."""
    if not isinstance(n, Num):
        return None
    m, e = n
    if m < 0:
        return None
    if m == 0:
        return 0
    if e >= 0:
        return m * 10 ** e if e <= 18 else None
    q, r = divmod(m, 10 ** (-e))
    return q if r == 0 else None


class Canon:
    """decode∘encode for documents.  `brk` names a deliberate oracle bug."""

    def __init__(self, brk: str | None = None):
        self.brk = brk

    def unknown(self, known, kvs):
        ext = [(k, v) for k, v in kvs if k not in known]
        if self.brk == "sort-unknown":
            ext.sort(key=lambda p: p[0])
        if self.brk == "drop-unknown":
            ext = []
        return ext

    def strs(self, xs):
        if not isinstance(xs, list) or not all(isinstance(x, str) for x in xs):
            raise DecodeError("list of strings")
        return list(xs)

    def strmap(self, o):
        if not isinstance(o, Obj):
            raise DecodeError("object")
        out = []
        for k, v in o:
            if not isinstance(v, str):
                raise DecodeError("string value")
            out.append((k, v))
        return out

    def variant(self, v):
        if not isinstance(v, Obj):
            raise DecodeError("variant object")
        cond = v.get("if")
        if v.has("if") and not isinstance(cond, str):
            raise DecodeError("if")
        to = v.get("to")
        if not isinstance(to, str):
            raise DecodeError("to")
        resume = v.get("resume", False) if v.has("resume") else False
        if not isinstance(resume, bool):
            raise DecodeError("resume")
        calls = self.strs(v.get("do")) if v.has("do") else []
        if v.has("do") and not isinstance(v.get("do"), list):
            raise DecodeError("do")
        out = Obj()
        if v.has("if"):
            out.append(("if", cond))
        out.append(("to", to))
        if resume or (self.brk == "keep-resume-false" and v.has("resume")):
            out.append(("resume", resume))
        if calls:
            out.append(("do", list(calls)))
        out.extend(self.unknown(VARIANT_KEYS, v))
        return out

    def on(self, kvs):
        if not kvs.has("on"):
            return Obj()
        o = kvs.get("on")
        if not isinstance(o, Obj):
            raise DecodeError("on")
        out = Obj()
        for ev, vs in o:
            if not isinstance(vs, list):
                raise DecodeError("variants")
            out.append((ev, [self.variant(x) for x in vs]))
        return out

    def node(self, n):
        if not isinstance(n, Obj):
            raise DecodeError("node object")
        on = self.on(n)
        if n.has("children"):
            ch = n.get("children")
            if not isinstance(ch, Obj):
                raise DecodeError("children")
            kids = Obj((k, self.node(x)[0]) for k, x in ch)
            if n.has("timers"):
                raise DecodeError("section timers")
            init = n.get("initial")
            if not isinstance(init, str):
                raise DecodeError("initial")
            out = Obj([("initial", init)])
            if on:
                out.append(("on", on))
            out.append(("children", kids))
            out.extend(self.unknown(NODE_KEYS, n))
            return out, True
        if n.has("initial"):
            raise DecodeError("initial without children")
        ts = Obj()
        if n.has("timers"):
            t = n.get("timers")
            if not isinstance(t, Obj):
                raise DecodeError("timers")
            for k, v in t:
                nat = num_to_nat(v)
                if nat is None:
                    raise DecodeError("timer value")
                ts.append((k, v if self.brk == "keep-num-text" else Num(nat, 0)))
        out = Obj()
        if on:
            out.append(("on", on))
        if ts:
            out.append(("timers", ts))
        out.extend(self.unknown(NODE_KEYS, n))
        return out, False

    def doc(self, d):
        if not isinstance(d, Obj):
            raise DecodeError("doc object")
        if num_to_nat(d.get("figram")) != 1:
            raise DecodeError("figram")
        req = self.strs(d.get("requires")) if d.has("requires") else []
        if any(f not in KNOWN_FEATURES for f in req):
            raise DecodeError("requires")
        decls = {}
        for k in ("conditions", "calls", "variables"):
            decls[k] = self.strmap(d.get(k)) if d.has(k) else []
        if not d.has("bot"):
            raise DecodeError("bot")
        bot, is_sec = self.node(d.get("bot"))
        if not is_sec:
            raise DecodeError("bot is a message")
        out = Obj([("figram", Num(1, 0))])
        if req:
            out.append(("requires", req))
        for k in ("conditions", "calls", "variables"):
            if decls[k]:
                out.append((k, Obj(decls[k])))
        out.append(("bot", bot))
        out.extend(self.unknown(DOC_KEYS, d))
        return out


def oracle_fmt(text: str, brk: str | None = None):
    """Expected stdout of `figram fmt` (bytes) or None if it must fail."""
    try:
        j = load_j(text)
        return (render(Canon(brk).doc(j)) + "\n").encode()
    except (Reject, ValueError, DecodeError):
        return None
