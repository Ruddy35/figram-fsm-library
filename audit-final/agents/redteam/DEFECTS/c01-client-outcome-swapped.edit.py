# c01: the Python client builds the outcome event with action and outcome swapped
EDITS = [("clients/python/figram_client.py",
"return self.step(key, snapshot, outcome_event(action, outcome), guards, check_token(token))",
"return self.step(key, snapshot, outcome_event(outcome, action), guards, check_token(token))")]
