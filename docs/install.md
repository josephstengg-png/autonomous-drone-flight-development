# Installing ARTEX

A site has one **ground-station laptop** (the console, the planner, the assistant) and one **companion computer**
(Jetson) per aircraft, wired to the laptop, each holding its aircraft's radio link (ELRS backpack) and, for bench work,
the flight controller on USB.

## 1. The laptop (Ubuntu 22.04 or 24.04)

From the offline bundle (no internet needed):

```bash
tar xzf artex-<ver>-laptop-offline.tar.gz
cd artex-<ver>-laptop && ./install-laptop.sh          # -> ~/.local/share/artex-app, command artex-console
```

or the Debian package: `sudo apt install ./artex-sar_<ver>_amd64.deb` (installs to `/opt/artex`).

Check `sha256sum -c SHA256SUMS` before installing; if the release is signed, `gpg --verify SHA256SUMS.asc` first.

Start with `artex-console` and open http://127.0.0.1:8787. The first start creates the data folder
(`~/.local/share/artex`, or `ARTEX_HOME`) with sample areas and missions, and asks you to create the first operator
(the admin). `artex-console --demo` runs everything against simulated aircraft.

### Site configuration (in the data folder)

| File | What |
|---|---|
| `aircraft.yaml` | your aircraft: sysid, companion host, radio/USB links, fleet file, hover thrust (template in `site-examples/`) |
| `companions.yaml`, `artex-registry.yaml` | your companions and transmitters (templates in `site-examples/`) |
| `fleet-*.yaml` | one-aircraft and all-aircraft fleet files used by preflight and the out-of-band abort |
| `operators.json` | created by the console: operators, roles, salted PIN hashes. Keep it private. |

### Environment settings

| Variable | Default | Use |
|---|---|---|
| `ARTEX_HOME` | `~/.local/share/artex` | the data folder |
| `ARTEX_SAT_TILES`, `ARTEX_SAT_ATTRIBUTION` | Esri World Imagery | satellite layer of the mission map (a commercial product needs its own imagery licence) |
| `ARTEX_TILES`, `ARTEX_TILES_ATTRIBUTION` | OpenStreetMap | street layer and area editor (use your own tile server for production) |
| `ARTEX_LLM_URL`, `ARTEX_LLM_MODEL` | `http://127.0.0.1:11434`, `qwen3:4b-instruct` | the assistant's local model (Ollama) |
| `ARTEX_ASSISTANT_LIVE` | off | `enabled` lets operators delegate gates to the assistant on REAL aircraft; leave off until the evaluation passes on site |

### The assistant's model (optional)

Install Ollama, then `ollama pull qwen3:4b-instruct` (Qwen3-4B-Instruct-2507, Apache-2.0, ~2.5 GB). Use the *instruct*
tag: the plain `qwen3:4b` tag is an always-thinking build that is slow and writes its reasoning into answers. Before
allowing delegation on real aircraft: `python -m artex.assistant.evals --phase BC --repeat 3` must report `"ok": true`.

## 2. Each companion (Jetson, Ubuntu 24.04, aarch64, no internet)

Copy `artex-<ver>-companion.tar.gz` to the Jetson over the wired network, then on the Jetson:

```bash
tar xzf artex-<ver>-companion.tar.gz && cd artex-<ver>-companion && ./install-companion.sh
```

It installs the agent into `~/artex_app` and its Python packages from the bundle, and prints the fingerprint, which
must match `FINGERPRINT` (and the laptop's `artex-console` version). It does **not** move the radio relay service; to
run the relay from this install: `sudo bash ~/artex_app/artex/fleetops/elrs_service.sh`.

From the laptop instead: `python3 -m artex.fleetops.provision --all` copies the companion payload and checks it.

**The console refuses every live run while any companion's code differs from the laptop's** (version handshake).

### Fallback watchdog (recommended)

`./install-companion.sh --fallback` installs `artex-fallback.service` (edit it first): on the second companion, if the
laptop disappears for 15 s while an aircraft agent runs, it sets the HOLD (or LAND) flag on every companion; on the
third, only if the laptop and the second are both gone. It never clears a flag and never starts anything. It needs ssh
keys from that companion to its peers.
