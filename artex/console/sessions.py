"""Operator sign-in and the session that makes per-run authorisation quick (Phase A2, 12 Nov 2026).

Once per session: the operator signs in (name + PIN), names one pilot per aircraft, and accepts each profile's risks
once (remembered against the profile's hash, so editing the profile asks again). Per run: one confirmation tick and
one press-and-hold. What does not get quicker, because it is the point: the fresh per-run confirmation that props and
area are as stated, named pilots, and BLOCKs nobody can tick away (those stay in gs_core / gs_fleet).

PINs are stored only as salted PBKDF2 hashes in the site file `operators.json` (one per installation).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from artex.common.layout import ROOT

OPERATORS_FILE = ROOT / "operators.json"
SESSION_TTL_S = 12 * 3600
ROLES = ("observer", "operator", "admin")            # each includes the ones before it
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
PBKDF2_ROUNDS = 200_000


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _load() -> dict:
    try:
        return json.loads(OPERATORS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def has_operators() -> bool:
    return bool(_load())


def add_operator(name: str, pin: str, role: str = "operator") -> None:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", name):
        raise ValueError("Operator name: 2 to 40 letters.")
    if not re.fullmatch(r"\d{4,8}", pin or ""):
        raise ValueError("PIN: 4 to 8 digits.")
    if role not in ROLES:
        raise ValueError(f"Role: one of {', '.join(ROLES)}.")
    ops = _load()
    if name.lower() in {k.lower() for k in ops}:
        raise ValueError(f"{name} already exists.")
    salt = secrets.token_hex(16)
    ops[name] = dict(salt=salt, hash=_hash(pin, salt), role=role, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    OPERATORS_FILE.write_text(json.dumps(ops, indent=1) + "\n")
    OPERATORS_FILE.chmod(0o600)


def role_of(name: str) -> str:
    rec = _load().get(name) or {}
    return rec.get("role", "admin" if len(_load()) == 1 else "operator")    # files from before roles: first = admin


def check_pin(name: str, pin: str) -> str | None:
    """The operator's stored name if the PIN is right, else None (constant-time compare)."""
    for stored, rec in _load().items():
        if stored.lower() == (name or "").strip().lower():
            return stored if hmac.compare_digest(_hash(pin or "", rec["salt"]), rec["hash"]) else None
    return None


class Sessions:
    def __init__(self, now=time.time):
        self._s: dict[str, dict] = {}
        self._now = now

    def open(self, operator: str, role: str = "operator") -> str:
        tok = secrets.token_urlsafe(24)
        self._s[tok] = dict(operator=operator, role=role, pilots={}, accepted={}, opened=self._now())
        return tok

    def get(self, tok: str | None) -> dict | None:
        s = self._s.get(tok or "")
        if not s or self._now() - s["opened"] > SESSION_TTL_S:
            return None
        return s

    def close(self, tok: str) -> None:
        self._s.pop(tok, None)

    @staticmethod
    def set_crew(s: dict, pilots: dict) -> dict:
        clean = {}
        for k, v in (pilots or {}).items():
            n = str(v or "").strip()
            if n:
                if len(n) < 2:
                    raise ValueError(f"Pilot for ARTEX{k}: a name of at least 2 letters.")
                clean[str(int(k))] = n
        names = [n.lower() for n in clean.values()]
        if len(set(names)) != len(names):
            raise ValueError("One pilot per aircraft: the same person cannot hold two handsets.")
        s["pilots"] = clean
        return clean

    @staticmethod
    def accept(s: dict, profile_hash: str, risk_ids: list[str]) -> None:
        s["accepted"][profile_hash] = sorted(set(s["accepted"].get(profile_hash, [])) | set(risk_ids))

    @staticmethod
    def missing_acceptance(s: dict, profile_hash: str, needed: set[str]) -> list[str]:
        return sorted(needed - set(s["accepted"].get(profile_hash, [])))

    @staticmethod
    def missing_pilots(s: dict, sysids: list[int]) -> list[int]:
        return [x for x in sysids if not s["pilots"].get(str(x))]


def allowed(session: dict | None, need: str | None) -> bool:
    """need None = anyone; otherwise the session's role must rank at least `need`."""
    if need is None:
        return True
    return bool(session) and ROLE_RANK.get(session.get("role", "observer"), 0) >= ROLE_RANK[need]
