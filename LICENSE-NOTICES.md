# Third-party notices

| Component | Licence | How it is used |
|---|---|---|
| pymavlink | LGPL-3.0 | separate, unmodified, replaceable Python dependency (installed from PyPI / the offline wheel bundle). Its licence text ships with it. |
| PyYAML | MIT | dependency |
| matplotlib (optional, `plot`) | matplotlib licence (PSF-style) | mission plots |
| pyulog, numpy (optional, `ulog`) | BSD-3-Clause | flight-log analysis (diagnostics) |
| Qwen3-4B (Phase B) | Apache-2.0 | local language model for the assistant, run through Ollama / llama.cpp |
| Esri World Imagery (map background) | Esri terms of use | default satellite layer of the mission map, fetched live; a commercial product needs an Esri (ArcGIS) licence or another imagery provider: set `ARTEX_SAT_TILES` / `ARTEX_SAT_ATTRIBUTION` |
| OpenStreetMap tiles (street layer, area editor) | ODbL data; OSM tile usage policy | the public tile servers are not for commercial heavy use: set `ARTEX_TILES` to the customer's tile server |
| Leaflet 1.9.4, three.js 0.160.0 | BSD-2-Clause, MIT | bundled in `artex/console/static/vendor/` with their licence files |
| PX4 Autopilot | BSD-3-Clause | runs on the aircraft; not shipped |
| QGroundControl | Apache-2.0 / GPL-3.0 | optional telemetry display; not shipped |

The product's own code: proprietary (licence to be set before release).
