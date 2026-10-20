"""Build audit-final/PATCHES (format-patch series + bundle of all audit branches) and MANIFEST.json."""
import hashlib, json, os, subprocess, sys, datetime

REPO = "/home/user/figram-fsm-library"
D = os.path.join(REPO, "audit-final")


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True, text=True).stdout


branches = sorted(b.strip() for b in git("for-each-ref", "--format=%(refname:short)", "refs/heads/audit/").split())
fix_branches = sorted(b.strip() for b in git("for-each-ref", "--format=%(refname:short)", "refs/heads/fix/").split())
heads = {b: git("rev-parse", b).strip() for b in branches + fix_branches}

patches = os.path.join(D, "PATCHES")
os.makedirs(patches, exist_ok=True)
series = os.path.join(patches, "integration")
if os.path.isdir(series):
    for f in os.listdir(series):
        os.remove(os.path.join(series, f))
git("format-patch", "--quiet", "-o", series, "audit/baseline..audit/integration")
test_only = []
for f in sorted(os.listdir(series)):
    with open(os.path.join(series, f), encoding="utf-8", errors="replace") as fh:
        head = fh.read(4000)
    if "TEST-ONLY" in head.split("\n---\n")[0]:
        test_only.append(f)
with open(os.path.join(patches, "README.md"), "w", encoding="utf-8") as fh:
    fh.write("# Патчи аудита\n\n")
    fh.write("* `integration/` — `git format-patch audit/baseline..audit/integration` (HEAD `%s`): тесты раньше исправлений,\n"
             "  по одному коммиту на исправление, авторство и `(cherry picked from …)` сохранены.\n" % heads["audit/integration"])
    fh.write("* **TEST-ONLY** (адаптация под тулчейн Lean из исходников; в выпуск НЕ переносить):\n")
    for f in test_only:
        fh.write("  * `integration/%s`\n" % f)
    fh.write("* `audit-branches.bundle` — все ветки `audit/*` (тесты, инструменты, хосты, исправления каждого агента)\n"
             "  и ветки волны исправлений `fix/*` (`fix/final` влит в опубликованную ветку, см. FIXES.md):\n\n"
             "  ```\n  git fetch PATCHES/audit-branches.bundle 'refs/heads/audit/*:refs/heads/audit/*' 'refs/heads/fix/*:refs/heads/fix/*'\n  ```\n\n")
    fh.write("| ветка | HEAD |\n|---|---|\n")
    for b in branches + fix_branches:
        fh.write("| `%s` | `%s` |\n" % (b, heads[b]))
    fh.write("\nПрименение исправлений к baseline без TEST-ONLY:\n\n```\ngit checkout -b fixes d69bbaf\n"
             "ls PATCHES/integration/*.patch | grep -v -e %s | xargs git am\n```\n"
             % " -e ".join(t.split("-", 1)[0] for t in test_only) if test_only else "")
bundle = os.path.join(patches, "audit-branches.bundle")
if os.path.exists(bundle):
    os.remove(bundle)
git("bundle", "create", bundle, *branches, *fix_branches)

files = {}
for root, _, names in os.walk(D):
    for n in names:
        p = os.path.join(root, n)
        rel = os.path.relpath(p, D)
        if rel == "MANIFEST.json":
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        files[rel] = h.hexdigest()

RELEASE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "release.json"), encoding="utf-8"))
manifest = {
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "archive": {"name": "figram-lean_4.zip",
                "sha256": "7769c0030bb2c516060015039263764d778ead91a92d8b2213f4fa1d9f8d1329"},
    "baseline_commit": heads.get("audit/baseline"),
    "repository": "Ruddy35/figram-fsm-library",
    "published_branch": "claude/kind-ramanujan-n4q8mg",
    "toolchains": {
        "official": "leanprover/lean4:v4.35.0 via elan in GitHub Actions run 39330382243 (verify.yml), 39330382251",
        "from_source": "lean4 tag v4.35.0 commit eb6cafb7290e8bd5f2937522d8d804f58f02bafc, gcc 13.3, cmake --preset release",
        "go": "go1.24.7 (CI go1.23.12)", "python": "3.13.16", "jsonschema": "4.26.0",
    },
    "machine": "KVM 4 vCPU Intel Xeon 2.8GHz, 16 GB, Ubuntu 24.04.5, Linux 6.18.44 (shared by agents)",
    "audited_archive_bin_sha256": {"bin/figram": "a08c666cc298264990175b91000884b9a93afbbc58185a96343541f31af79787",
                                   "bin/figram-engine": "8d96cf275ba194540b95caaefa03ab15e0bc01c06081ddfe5f7548fac680741f"},
    "release": RELEASE,
    "branches": heads,
    "test_only_patches": test_only,
    "file_count": len(files),
    "files_sha256": dict(sorted(files.items())),
}
with open(os.path.join(D, "MANIFEST.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=1)
print(f"branches={len(branches)} patches={len(os.listdir(series))} test_only={test_only} files={len(files)} "
      f"bundle={os.path.getsize(bundle)} bytes")
