"""`failed` after "no answer" is not "not performed" (docs/host-contract.md, section 2,
"Outcomes"; docs/format.md, section 7, decision 12).

The reference host tests/engine/host_sim.py (imported unchanged) follows the contract: after
MAX_ATTEMPTS attempts without an answer it delivers `failed`. Its CRM, when "down", raises
before doing anything. A real CRM can time out AFTER it created the order (the answer is lost on
the way back). This driver replaces only that: for the users whose CRM is "down", the first
attempt creates the order and then the answer is lost, and the next attempts with the same
idempotency key time out as well. The host delivers `failed`, the pizza document leads to
`order_failed`, the user presses "Retry", which is a new call with a new token and a new
idempotency key: a second order.

    python3 failed_unknown.py REPO
"""
import os, sys

repo = sys.argv[1]
sys.path.insert(0, os.path.join(repo, "tests", "engine"))
import host_sim  # noqa


class LossyCRM(host_sim.CRM):
    def call(self, key, user, name, data):
        with self._lock:
            lossy = self.down.get(user, 0) > 0
            if lossy:
                self.down[user] -= 1
                if key not in self.executed and name == "create_order":
                    # performed, then the answer is lost (a timeout on the way back)
                    self.executed[key] = (user, name, data, ("created", {"order": key}))
        if lossy:
            raise host_sim.Down("the CRM does not answer (the order was created)")
        return super().call(key, user, name, data)   # unchanged behaviour otherwise

host_sim.CRM = LossyCRM
ok = host_sim.scenario_orders(os.path.join(repo, ".lake/build/bin/figram-engine"),
                              os.path.join(repo, "examples/pizza.bot.json"), users=10, crash_rate=0.0)
print("scenario result:", "as expected" if ok else "NOT as expected (duplicate orders after `failed`)")
sys.exit(0 if not ok else 1)
