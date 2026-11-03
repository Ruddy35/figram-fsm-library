"""Which events does host_sim's lost-snapshot scenario drop for a user without a snapshot?
host_sim's docstring says "a timer or outcome event of a lost snapshot is dropped".
    python3 lost_dropped.py REPO"""
import collections, os, sys
repo = sys.argv[1]
sys.path.insert(0, os.path.join(repo, "tests", "engine"))
import host_sim  # noqa
hosts = []
Orig = host_sim.Host
class Spy(Orig):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); hosts.append(self)
host_sim.Host = Spy
ok = host_sim.scenario_lost_snapshot(os.path.join(repo, ".lake/build/bin/figram-engine"),
                                     os.path.join(repo, "examples/pizza.bot.json"))
kinds = collections.Counter(ev.split(":")[0] for h in hosts for _, ev in h.dropped)
print("scenario ok:", ok, "dropped events by kind:", dict(kinds))
