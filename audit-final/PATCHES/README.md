# Патчи аудита

* `integration/` — `git format-patch audit/baseline..audit/integration` (HEAD `8be73ac2a9f76ba7dae0e3e6ec503b312d79a18f`): тесты раньше исправлений,
  по одному коммиту на исправление, авторство и `(cherry picked from …)` сохранены.
* **TEST-ONLY** (адаптация под тулчейн Lean из исходников; в выпуск НЕ переносить):
  * `integration/0039-audit-TEST-ONLY-adaptation-of-ffi-build.sh-and-verif.patch`
  * `integration/0040-audit-TEST-ONLY-ffi-audit-build_audit.sh-uses-the-si.patch`
* `audit-branches.bundle` — все ветки `audit/*` (тесты, инструменты, хосты, исправления каждого агента)
  и ветки волны исправлений `fix/*` (`fix/final` влит в опубликованную ветку, см. FIXES.md):

  ```
  git fetch PATCHES/audit-branches.bundle 'refs/heads/audit/*:refs/heads/audit/*' 'refs/heads/fix/*:refs/heads/fix/*'
  ```

| ветка | HEAD |
|---|---|
| `audit/baseline` | `d69bbaf7881a0ae1c11b75a35cf5193ad604b925` |
| `audit/core-compiler` | `3d73782393c47423d5b45a454c65e86d989b0976` |
| `audit/cross-verify` | `2b98e14050f5849665782ed57ba82675adf82074` |
| `audit/engine-timers` | `c6eecd068ca43937abc368f9f334246590481e14` |
| `audit/ffi-go` | `a0279052744d26ea3e89f519ba24f898239b511f` |
| `audit/formats-correctness` | `4a38b911652c3e5e85eb87a54d2f7a1f89f944c0` |
| `audit/formats-security` | `d69bbaf7881a0ae1c11b75a35cf5193ad604b925` |
| `audit/harness-ci` | `1cd4a74e474456b3d13ff70bb476b41445276e93` |
| `audit/host-go` | `a5c061bd3298d6f939eead1d2073fefb80b16c76` |
| `audit/host-perf` | `149515fc2e31a7f193c9edd8f5b3da1130c6578e` |
| `audit/host-python` | `3c922e62f896fd3e53b7ee8f02a62080651136e4` |
| `audit/integration` | `8be73ac2a9f76ba7dae0e3e6ec503b312d79a18f` |
| `audit/integration-cc` | `a291e1fc7c18c7a192eb6e4991c42ffbfc70be2d` |
| `audit/integration-docs` | `8ec1006a3e0607827e60c00685d1b339da8a3aa7` |
| `audit/integration-fc` | `dc1584de54c105f5c5cb500e57f2ef9b703a44cb` |
| `audit/integration-ffi` | `acf677468f36b8455b41ef4b444ede806534850f` |
| `audit/integration-pf` | `7ffca38b2fa2ea8a7b9b67305a85a4f7ee97cb37` |
| `audit/integration-py` | `02166af20563e9cc209c33833c1ce265f0996c62` |
| `audit/proofs` | `b5bc7e9e82de403629bcbf65576dddb56342ef8f` |
| `audit/python-client` | `d8f658bd27eed701857c33a0f927271370729184` |
| `audit/ref` | `d69bbaf7881a0ae1c11b75a35cf5193ad604b925` |
| `audit/ref-adapted` | `d8a548b2f027314d185b1e09f477215936c894e6` |
| `fix/base` | `8be73ac2a9f76ba7dae0e3e6ec503b312d79a18f` |
| `fix/client` | `031a6e51dff1e644fd7671f351599adbde67f149` |
| `fix/core` | `42af993e29fb51b0a94d4247216ea69775c04dd5` |
| `fix/docs` | `f4531cab100902e31414f9fb8b42c1713b4135da` |
| `fix/ffi` | `9ba1932c78ee28966863baae2377465fb2614dcd` |
| `fix/final` | `8e580aa969f9bf72f170df11737b8bacfeaf5338` |
| `fix/memory` | `79eb8aa7179be634f0d59e31b64d7a9e8949df16` |
| `fix/protocol` | `db974ec2330219fa6ea7fe1fb06bd4400b5203f7` |

Применение исправлений к baseline без TEST-ONLY:

```
git checkout -b fixes d69bbaf
ls PATCHES/integration/*.patch | grep -v -e 0039 -e 0040 | xargs git am
```
