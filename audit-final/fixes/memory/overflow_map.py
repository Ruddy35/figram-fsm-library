"""Исход движка на 250 вложенных объектах при разном RLIMIT_STACK: код выхода и «m» — сообщение о переполнении.
    python3 overflow_map.py ENGINE [ENGINE…]   (по 8 запусков; с окружением родителя и с пустым)"""
import resource, subprocess, sys, tempfile
doc = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
doc.write('{"figram":1,"bot":' + '{"a":' * 250 + "1" + "}" * 250 + "}"); doc.close()
for engine in sys.argv[1:]:
    for label, env in (("окружение родителя", None), ("пустое окружение", {})):
        print(f"== {engine}, {label}")
        for kb in (12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80):
            out = []
            for _ in range(8):
                p = subprocess.run([engine, doc.name], input=b'{"id":1,"op":"hello"}\n', capture_output=True, env=env,
                                   preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_STACK, (kb << 10, resource.RLIM_INFINITY)))
                out.append(str(p.returncode) + ("m" if b"Stack overflow detected" in p.stderr else ""))
            print(f"{kb:3} КиБ: {' '.join(out)}", flush=True)
