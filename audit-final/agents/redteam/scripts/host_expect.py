#!/usr/bin/env python3
"""The expect checks verify.sh applies to the host_sim.py output (copied from verify.sh)."""
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
checks = [
 (r'^  users (\d+): outcome as expected \(.*\) for (\d+), orders in the CRM (\d+), events processed (\d+) of (\d+) .*lost updates (\d+), actions not run (\d+)$',
  'g[0] > 0 and g[0] == g[1] and 0 < g[2] <= g[0] and g[3] == g[4] and g[5] == 0 and g[6] == 0', "host contract"),
 (r'^  outcomes: calls with a token (\d+), outcome events lost (\d+), processed twice (\d+), without a call (\d+), waits left (\d+) \(created (\d+), rejected (\d+), failed (\d+)\); background actions run (\d+) of (\d+), in the failure queue (\d+)$',
  'g[0] > 0 and g[1] == g[2] == g[3] == g[4] == 0 and g[6] > 0 and g[7] > 0 and g[5] + g[6] + g[7] == g[0] and 0 < g[8] == g[9] and g[10] == 0', "outcomes"),
 (r'^background actions \(host by the contract\), failures injected \d+: users (\d+), kitchen down for (\d+), users not held up (\d+), blocked (\d+), orders (\d+), failure queue (\d+) notify_kitchen with an alert, other failures (\d+), sequential actions not run (\d+)$',
  'g[0] > 0 and 0 < g[1] < g[0] and g[2] == g[0] == g[4] and g[3] == 0 and g[5] == g[1] and g[6] == g[7] == 0', "background"),
]
bad = 0
for rx, cond, what in checks:
    m = re.search(rx, text, re.M)
    if not m:
        print(f"FAIL {what}: no line"); bad += 1; continue
    g = [int(re.sub(r"[^0-9-]", "", x)) for x in m.groups()]
    if not eval(cond, {}, {"g": g}):
        print(f"FAIL {what}: {m.group(0)[:160]}"); bad += 1
n = len(re.findall(r'^  the test catches the violation: yes( \(control attempts: [0-9]+\))?$', text, re.M))
if n != 3: print(f"FAIL controls: {n} of 3"); bad += 1
if not re.search(r'^  the test catches the violations: yes$', text, re.M): print("FAIL lost-snapshot controls"); bad += 1
print("gate-expect-ok" if bad == 0 else f"gate-expect-bad={bad}")
sys.exit(1 if bad else 0)
