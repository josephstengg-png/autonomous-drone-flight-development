"""The app's version and a fingerprint of the code a companion runs, for the console's version handshake.

    python3 -m artex.common.version                # prints {"version": ..., "fingerprint": ...}

The fingerprint is a hash of the companion payload (artex.common.layout.COMPANION_PAYLOAD), so a companion that
carries the same version number but older agent code is still caught (10 Nov 2026: jetson-1 ran an October agent).
"""
from __future__ import annotations

import hashlib
import json
import sys

from artex import __version__
from artex.common.layout import COMPANION_PAYLOAD, ROOT


def fingerprint(root=ROOT) -> str:
    h = hashlib.sha256()
    for rel in COMPANION_PAYLOAD:
        p = root / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def report() -> dict:
    return dict(version=__version__, fingerprint=fingerprint())


if __name__ == "__main__":
    print(json.dumps(report()))
    sys.exit(0)
