"""The only place in the codebase that talks to FPP."""
from .adapter import (
    STATUS_IDLE,
    STATUS_PLAYING,
    STATUS_STOPPING,
    STATUS_UNKNOWN,
    TESTED_FPP_VERSIONS,
    UNREACHABLE,
    FppAdapter,
    FppError,
    PlaylistEntry,
    ShowStatus,
    major_minor,
    untested_version_warning,
)
from .fake import FakeFppAdapter, from_catalog
from .http import HttpFppAdapter, parse_playlist, parse_status
from .mqtt import MqttFppAdapter

__all__ = [
    "FppAdapter", "FppError", "PlaylistEntry", "ShowStatus",
    "HttpFppAdapter", "MqttFppAdapter", "FakeFppAdapter", "from_catalog",
    "parse_status", "parse_playlist",
    "TESTED_FPP_VERSIONS", "UNREACHABLE", "major_minor",
    "untested_version_warning",
    "STATUS_IDLE", "STATUS_PLAYING", "STATUS_STOPPING", "STATUS_UNKNOWN",
]
