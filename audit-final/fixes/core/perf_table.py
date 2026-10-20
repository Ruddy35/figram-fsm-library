import json, sys
L = "/home/user/figram-audit/fixes/core/LOGS"
base = {}
for f in [f"{L}/perf-base.jsonl", f"{L}/10-P06-P07-perf-RED-base.jsonl"]:
    for l in open(f):
        r = json.loads(l); b = base.setdefault(r["scenario"], {})
        b["load"] = r["load"]["cpu_s"]
        if r.get("check"): b["check"] = r["check"]["cpu_s"]
        for k, v in r.get("step", {}).items():
            if isinstance(v, dict): b.setdefault("steps", {})[k if ":" in k else "start:" + k] = v["cpu_us"]
fin = {}
for f in [f"{L}/final/04-perf-GREEN.jsonl", f"{L}/final/05-perf-extra.jsonl"]:
    for l in open(f):
        r = json.loads(l); b = fin.setdefault(r["scenario"], {})
        b["load"] = r["load"]["cpu_s"]; b["check"] = r.get("check", {}).get("cpu_s")
        b["steps"] = {k: v["cpu_us"] for k, v in r.get("step", {}).items() if isinstance(v, dict)}
print("| сценарий | загрузка до, с CPU | после | check до | после | шаг до (худший), мкс | после |")
print("|---|---|---|---|---|---|---|")
for s in fin:
    b = base.get(s, {}); a = fin[s]
    wb = max(b.get("steps", {}).values(), default=None); wa = max(a["steps"].values(), default=None)
    print(f"| {s} | {b.get('load','—')} | {a['load']} | {b.get('check','—')} | {a['check']} | {wb if wb is not None else '—'} | {wa} |")
