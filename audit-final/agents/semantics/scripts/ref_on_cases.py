import json, sys
sys.path.insert(0, "/home/user/figram-audit/agents5/semantics/scripts")
import prose_ref as P
R = "/home/user/figram-audit/agents5/semantics/REPRODUCTIONS"
bad = 0; n = 0
for doc, case in [("outcomes","out"),("stay","stay"),("bg","bg"),("recneed","recneed"),("nested","nested")]:
    D = P.Doc(json.load(open(f"{R}/docs/{doc}.bot.json")))
    exp = [json.loads(l) for l in open(f"{R}/cases/{case}.expected.jsonl") if not l.startswith('#')]
    for req, e in zip((json.loads(l) for l in open(f"{R}/cases/{case}.req.jsonl")), exp):
        n += 1
        try:
            r = P.reply(D, req)
        except Exception as ex:
            r = {"id": req.get("id"), "code": "bad_request(ref:%s)" % type(ex).__name__}
        if P.normalize(r) != P.normalize(e):
            bad += 1; print(case, "REF DIFFERS", json.dumps(req)[:150], "\n   ref:", json.dumps(r), "\n   exp:", json.dumps(e))
print("cases", n, "ref differs", bad)
