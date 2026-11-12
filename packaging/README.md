# Packaging

| Bundle | For | Contents | Status |
|---|---|---|---|
| `artex-<ver>-*.whl` | ground-station laptop | the whole `artex` package: console + GUI, planner, fleet ops, (assistant) | built by `build_bundles.sh` |
| `artex-<ver>-companion.tar.gz` | each Jetson | `artex.common.layout.COMPANION_PAYLOAD`: agent, guard, relay, on-board fleet tools | built by `build_bundles.sh` |
| `.deb` (laptop, Ubuntu 22.04/24.04) | install on a clean laptop | wheel + venv + systemd-free launcher + desktop entry | to do |
| Jetson offline bundle | Jetsons have no internet | companion tarball + aarch64/py3.12 wheels (pymavlink, PyYAML) + `elrs-link` systemd unit | to do |
| Model bundle | laptop (and Jetsons, fallback only) | Qwen3-4B GGUF (Apache-2.0) + runtime config | Phase B |

Install on a companion (today, from the laptop): `python3 -m artex.fleetops.provision --all` copies the companion
payload to `~/artex_app` on each Jetson and installs the relay service (`artex/fleetops/elrs_service.sh`).

The console's **version handshake** compares each companion's fingerprint (`python3 -m artex.common.version`) with its
own before any live run and refuses on a mismatch.
