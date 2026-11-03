# c02: the Python protocol validator accepts `detached` other than true
EDITS = [("clients/python/protocol.py",
"            if \"detached\" in a and a[\"detached\"] is not True:",
"            if \"detached\" in a and a[\"detached\"] not in (True, False):")]
