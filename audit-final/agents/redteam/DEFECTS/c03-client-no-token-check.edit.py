# c03: the Python client does not check the outcome token before sending
EDITS = [("clients/python/figram_client.py",
"return self.step(key, snapshot, outcome_event(action, outcome), guards, check_token(token))",
"return self.step(key, snapshot, outcome_event(action, outcome), guards, token)")]
