import json
for name, tail in [("space", " "), ("tab", "\t"), ("lf", "\n"), ("crlf", "\r\n"), ("vt", "\v"), ("ff", "\f"), ("nel", "\u0085"), ("nbsp", " "), ("x", "x")]:
    try:
        json.loads('{"id":1}' + tail); ok = True
    except ValueError:
        ok = False
    print(f"{name:6s} accepted={ok}")
