"""Settings, from the environment with usable defaults.

Every default works on a laptop, so the service runs with no configuration at
all. The Pi overrides what it needs from the systemd unit.

Read via Config.from_env() rather than at import time, so tests can build a
Config directly and the values are not frozen the moment the module loads.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "data" / "fppvote.db"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    db_path: Path = DEFAULT_DB
    fpp_url: str = "http://localhost"

    # Empty means "no MQTT" — it is optional in FPP's settings, so it can never
    # be required here either.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_hostname: str = "FPP"

    # 1 Hz. The old plugin polled at the same rate but blocked the main loop
    # doing it; this one offloads to a thread, and MQTT usually beats it to the
    # change anyway.
    poll_seconds: float = 1.0

    # Fire start_at_item this long before the current song ends, so the winner
    # starts clean. See CLAUDE.md section 7. Zero means "let FPP advance, then
    # jump" — the fallback behaviour, chosen deliberately.
    handover_lead_seconds: float = 2.0

    # How long a playlist read is reused before being refetched.
    playlist_cache_seconds: float = 60.0

    # Run against FakeFppAdapter instead of a real FPP. This is what makes
    # stages 1-6 buildable on a laptop with no Pi anywhere.
    fake_fpp: bool = False

    # Shared secret the admin page must send back as X-Admin-Token. Empty
    # (the default, and what every laptop run gets) leaves /api/admin open —
    # fine on a laptop with nothing listening but you, but this must be set
    # before the Cloudflare Tunnel goes up, or anyone with the URL can change
    # vote allowances and categories.
    admin_token: str = ""

    def __post_init__(self):
        object.__setattr__(self, "db_path", Path(self.db_path))

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            db_path=Path(os.environ.get("FPPVOTE_DB", DEFAULT_DB)),
            fpp_url=os.environ.get("FPPVOTE_FPP_URL", "http://localhost"),
            mqtt_host=os.environ.get("FPPVOTE_MQTT_HOST", ""),
            mqtt_port=_int("FPPVOTE_MQTT_PORT", 1883),
            mqtt_hostname=os.environ.get("FPPVOTE_MQTT_FPP_HOSTNAME", "FPP"),
            poll_seconds=_float("FPPVOTE_POLL_SECONDS", 1.0),
            handover_lead_seconds=_float("FPPVOTE_HANDOVER_LEAD", 2.0),
            playlist_cache_seconds=_float("FPPVOTE_PLAYLIST_CACHE", 60.0),
            fake_fpp=_bool("FPPVOTE_FAKE", False),
            admin_token=os.environ.get("FPPVOTE_ADMIN_TOKEN", ""),
        )
