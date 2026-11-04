#!/usr/bin/env python3
"""What a restore from a backup does to users: updates processed in the lost history that the
restored history never processed (Telegram does not deliver a confirmed update again), and users
whose chat shows a message of the lost history."""
import json, sqlite3, sys
run = sys.argv[1]
L = sqlite3.connect(f"file:{run}/host.lost.db?mode=ro", uri=True)
H = sqlite3.connect(f"file:{run}/host.db?mode=ro", uri=True)
B = sqlite3.connect(f"file:{run}/host.bak.db?mode=ro", uri=True)
lost = {(u, i) for u, i in L.execute("SELECT user, uid FROM processed WHERE uid LIKE 'tg:%'")}
bak = {(u, i) for u, i in B.execute("SELECT user, uid FROM processed WHERE uid LIKE 'tg:%'")}
final = {(u, i) for u, i in H.execute("SELECT user, uid FROM processed WHERE uid LIKE 'tg:%'")}
after_backup = lost - bak
redelivered = after_backup & final
never = after_backup - final
lost_calls = L.execute("SELECT COUNT(*) FROM outbox WHERE kind='call' AND token IS NOT NULL").fetchone()[0] - \
    B.execute("SELECT COUNT(*) FROM outbox WHERE kind='call' AND token IS NOT NULL").fetchone()[0]
X = sqlite3.connect(f"file:{run}/ext.db?mode=ro", uri=True)
keys_final = {k for (k,) in H.execute("SELECT key FROM outbox")}
orphan = [k for k, r in X.execute("SELECT key, result FROM effects WHERE result IN ('created','sent','booked')") if k not in keys_final]
print(json.dumps({"updates_processed_after_backup_in_lost_history": len(after_backup),
                  "of_them_processed_again_after_restore": len(redelivered),
                  "of_them_never_processed_again": len(never),
                  "calls_with_token_issued_in_lost_history": lost_calls,
                  "positive_external_effects_whose_call_is_not_in_the_restored_history": len(orphan),
                  "users_affected": len({u for u, _ in after_backup})}, indent=1))
