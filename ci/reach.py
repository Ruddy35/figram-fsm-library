"""Reachability of code and data in a built executable (ELF x86-64, PIE), using binutils.

    python3 ci/reach.py FILE [--unreachable RX…] [--reachable RX…]
        for the symbols whose name (or name after c++filt) fully matches a regular expression:
        REACHABLE with the shortest path from a root, or UNREACHABLE. Exit status 1 if a symbol of
        --unreachable is reachable or no symbol of --reachable is (an expression that matches no
        symbol in --reachable fails too: the check would be empty).
    python3 ci/reach.py --calls FILE SYMBOL
        the functions that SYMBOL calls directly (call, jmp), in order; FILE is an executable or an
        object file.

The graph. A node is a symbol of the symbol table (a function or a data object; aliases at one
address are one node; a symbol without a size extends to the next symbol). The edges are all
addresses referenced by the code (call, jmp, lea, mov… in `objdump -d`) and the pointers in data:
in a PIE every pointer to its own code or data is written by a dynamic relocation
(R_X86_64_RELATIVE, IRELATIVE; `readelf -r`). The roots are the entry point, main, _init, _fini,
.preinit_array, .init_array, .fini_array, the symbols of the dynamic table (shared libraries may
call them) and the pointer targets stored outside known symbols.
An over-approximation: an unreachable symbol is not executed or read by any path, except jumps to
addresses computed at run time from something other than the code and data of the file (dlsym and
the like). A file that is not a PIE (its data pointers have no relocations and are invisible) is
refused with exit status 2.
"""
import bisect, collections, re, subprocess, sys


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def demangle(names):
    out = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True, text=True).stdout.split("\n")
    return dict(zip(names, out[:len(names)]))


def calls(path, symbol):
    """Direct calls and jumps from the function symbol: in an object file by the relocations, in an
    executable by the target address (jumps inside the function, <symbol+0x…>, are skipped)."""
    lines = run("objdump", "-dr", "--no-show-raw-insn", f"--disassemble={symbol}", path).splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"\s*[0-9a-f]+:\s+(call|jmp)\w*\s+(.*)", line)
        if not m:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        r = re.match(r"\s*[0-9a-f]+: R_X86_64_\w+\s+([^\s+-]+)", nxt)
        if r:
            out.append(r.group(1))
            continue
        t = re.search(r"<([^>+]+)>", m.group(2))
        if t and "+" not in m.group(2).split("<", 1)[1]:
            out.append(t.group(1).split("@")[0])
    names = demangle(out)
    return [names[n] for n in out]


def graph(path):
    if "DYN" not in run("readelf", "-h", path).split("Type:", 1)[1].splitlines()[0]:
        print(f"{path}: not a PIE: data pointers without relocations are invisible, no estimate is possible")
        sys.exit(2)
    # Nodes: start address → names; the end is the symbol's size or the start of the next symbol.
    named = collections.defaultdict(set)
    size = {}
    for line in run("nm", "-S", "--defined-only", path).splitlines():
        f = line.split()
        if len(f) == 4 and f[2] in "TtWwDdBbRrVvi":
            a = int(f[0], 16); named[a].add(f[3]); size[a] = max(size.get(a, 0), int(f[1], 16))
        elif len(f) == 3 and f[1] in "TtWwDdBbRrVvi":
            a = int(f[0], 16); named[a].add(f[2]); size.setdefault(a, 0)
    starts = sorted(named)
    ends = [size[a] or (starts[i + 1] - a if i + 1 < len(starts) else 1) for i, a in enumerate(starts)]
    ends = [a + e for a, e in zip(starts, ends)]

    def owner(addr):  # the last symbol starting at or before addr that contains it (nesting is rare)
        i = bisect.bisect_right(starts, addr) - 1
        for j in range(i, max(i - 8, -1), -1):
            if addr < ends[j]:
                return starts[j]
        return None

    edges = collections.defaultdict(set)
    roots = set()
    # Pointers in data.
    arrays = []
    for line in run("readelf", "-SW", path).splitlines():
        m = re.search(r"\]\s+(\.init_array|\.fini_array|\.preinit_array)\s+\S+\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)", line)
        if m:
            a = int(m.group(2), 16); arrays.append((a, a + int(m.group(3), 16)))
    slot = {}
    for line in run("readelf", "-rW", path).splitlines():
        f = line.split()
        if len(f) >= 4 and f[2] in ("R_X86_64_RELATIVE", "R_X86_64_IRELATIVE"):
            where, t = int(f[0], 16), owner(int(f[3], 16))
            if t is None:
                continue
            slot[where] = t
            src = owner(where)
            if src is None or any(a <= where < b for a, b in arrays) or f[2] == "R_X86_64_IRELATIVE":
                roots.add(t)
            else:
                edges[src].add(t)
    # Code.
    ref = re.compile(r"\b([0-9a-f]+) <[^>]+>")
    cur = None
    dis = subprocess.Popen(["objdump", "-d", "--no-show-raw-insn", path], stdout=subprocess.PIPE, text=True)
    for line in dis.stdout:
        if line.endswith(">:\n"):
            cur = owner(int(line.split()[0], 16))
            continue
        if cur is None or "\t" not in line:
            continue
        for m in ref.finditer(line.split("\t", 1)[1]):
            a = int(m.group(1), 16)
            for t in (owner(a), slot.get(a)):
                if t is not None and t != cur:
                    edges[cur].add(t)
    if dis.wait():
        sys.exit("objdump failed")
    # Roots: the entry point, main, _init/_fini, the constructor and destructor arrays, the dynamic symbols.
    entry = int(re.search(r"Entry point address:\s+0x([0-9a-f]+)", run("readelf", "-h", path)).group(1), 16)
    roots |= {owner(entry)}
    for a, names in named.items():
        if names & {"main", "_init", "_fini"}:
            roots.add(a)
    for line in run("readelf", "--dyn-syms", "-W", path).splitlines():
        f = line.split()
        if len(f) >= 8 and f[6] != "UND" and f[3] in ("FUNC", "OBJECT", "IFUNC") and re.fullmatch(r"[0-9a-f]+", f[1]):
            o = owner(int(f[1], 16))
            if o is not None:
                roots.add(o)
    roots.discard(None)
    return named, edges, roots


def main(argv):
    if argv[:1] == ["--calls"] and len(argv) == 3:
        print("\n".join(calls(argv[1], argv[2])))
        return 0
    path, want = argv[0], {"--unreachable": [], "--reachable": []}
    mode = None
    for a in argv[1:]:
        if a in want:
            mode = a
        elif mode:
            want[mode].append(a)
        else:
            sys.exit(__doc__)
    named, edges, roots = graph(path)
    prev = {r: None for r in roots}
    queue = collections.deque(roots)
    while queue:
        s = queue.popleft()
        for t in edges.get(s, ()):
            if t not in prev:
                prev[t] = s; queue.append(t)
    pretty = demangle(sorted({n for ns in named.values() for n in ns}))
    label = {a: min(pretty[n] for n in ns) for a, ns in named.items()}
    print(f"{path}: nodes {len(named)}, roots {len(roots)}, reachable {len(prev)}")
    bad = 0
    for mode, rxs in want.items():
        for rx in rxs:
            hits = sorted({a for a, ns in named.items() for n in ns if re.fullmatch(rx, n) or re.fullmatch(rx, pretty[n])})
            live = [a for a in hits if a in prev]
            if mode == "--unreachable":
                ok = not live
                verdict = f"not in the file" if not hits else f"unreachable {len(hits) - len(live)} of {len(hits)}"
            else:
                ok = bool(live)
                verdict = f"not in the file" if not hits else f"reachable {len(live)} of {len(hits)}"
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {mode[2:]} '{rx}': {verdict}")
            for a in live[:3]:
                path_ = []
                while a is not None:
                    path_.append(label[a]); a = prev[a]
                print("      path: " + " ← ".join(path_[:14]))
    print(f"checks {sum(map(len, want.values()))}, failed {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
