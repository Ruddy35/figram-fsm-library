# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's security advisories:
[Report a vulnerability](https://github.com/Ruddy35/figram-fsm-library/security/advisories/new).
Do not open a public issue for a vulnerability. Include the affected version or commit, a
description of the impact, and the smallest input or program that reproduces it.

## Scope

In scope:

* `figram-engine` and the protocol handling: crashes, hangs, excessive memory or time on a
  request, replies that violate the protocol ([docs/format.md](docs/format.md));
* `figram` on untrusted documents;
* the C ABI and the shim (`ffi/`): memory safety, misuse that the contract says is detected but
  is not ([docs/embedding.md](docs/embedding.md));
* the reference clients in `clients/`;
* the build and release scripts, and the integrity of the release archives.

Out of scope:

* the behaviour that [docs/embedding.md](docs/embedding.md) lists as undefined, and the limits it
  documents (for example termination when memory runs out under `RLIMIT_AS`);
* hosts that do not follow [docs/host-contract.md](docs/host-contract.md) — for example a host
  that passes `timer:` events or tokens taken from user input to the engine;
* vulnerabilities in Lean, its runtime or other third-party components, which should be reported
  to their projects ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
