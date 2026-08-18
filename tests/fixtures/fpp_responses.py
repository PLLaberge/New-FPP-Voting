"""
FPP responses for the contract tests.

*** READ THIS BEFORE TRUSTING ANYTHING IN THIS FILE ***

These are CONSTRUCTED from FPP's documented API shapes. They have NOT been
captured from Paulin's Pi, because the machine writing them had no Pi to ask.
They are good enough to pin the parsing logic and to catch a regression in it;
they are NOT evidence that the field names match a real FPP.

To turn them into evidence:

    python3 tools/capture_fpp.py --host <pi-address>

That writes tests/fixtures/captured/*.json straight from the Pi, and
test_adapter.py picks those up automatically and runs the same assertions
against them. Until that file exists, the contract suite reports itself as
running on constructed data only — see test_captured_responses_are_present.

This is the exact failure the old plugin shipped: it declared support for FPP
versions nobody had run it against. A green test suite here means "the parser
is self-consistent", not "this works with FPP", until a capture exists.
"""

# --------------------------------------------------------------- /api/fppd/status
STATUS_PLAYING = {
    "fppd": "running",
    "mode": 2,
    "mode_name": "player",
    "status": 1,
    "status_name": "playing",
    "current_playlist": {
        "playlist": "Christmas 2025",
        "type": "both",
        "index": "7",
        "count": "65",
    },
    "current_sequence": "Wizards in Winter (Instrumental).fseq",
    "current_song": "12 Wizards in Winter (Instrumental).mp3",
    "current_song_title": "Wizards in Winter",
    "current_song_artist": "Trans-Siberian Orchestra",
    "seconds_played": 47,
    "seconds_remaining": 130,
    "time_elapsed": "00:47",
    "time_remaining": "02:10",
    "volume": 70,
}

STATUS_IDLE = {
    "fppd": "running",
    "mode": 2,
    "mode_name": "player",
    "status": 0,
    "status_name": "idle",
    "current_playlist": {"playlist": "", "type": "", "index": "0", "count": "0"},
    "current_sequence": "",
    "current_song": "",
    "seconds_played": 0,
    "seconds_remaining": 0,
    "volume": 70,
}

STATUS_STOPPING = {
    **STATUS_PLAYING,
    "status": 2,
    "status_name": "stopping gracefully",
}

# FPP reports a pause as status 4. The adapter maps it to 'playing' on purpose:
# the current song is still current, and reporting idle would make the service
# close the round and bin everyone's votes over a pause.
STATUS_PAUSED = {**STATUS_PLAYING, "status": 4, "status_name": "paused"}

# An older shape, to prove the defensive field reading earns its keep: no
# nested current_playlist, mm:ss strings instead of numeric seconds, and a
# different key for the sequence.
STATUS_OLD_SHAPE = {
    "fppd": "running",
    "status_name": "playing",
    "current_playlist_name": "Christmas 2025",
    "sequence_filename": "Wizards in Winter (Instrumental).fseq",
    "media_filename": "12 Wizards in Winter (Instrumental).mp3",
    "time_elapsed": "00:47",
    "time_remaining": "02:10",
}

# Something we simply do not understand. Must degrade, never raise.
STATUS_GARBAGE = {"unexpected": "shape", "from": "some future FPP"}

# ------------------------------------------------------------------ /api/system/info
SYSTEM_INFO = {
    "HostName": "FPP",
    "Platform": "Raspberry Pi",
    "Variant": "Raspberry Pi 4",
    "Version": "9.5",
    "Branch": "master",
}

SYSTEM_INFO_UNTESTED = {**SYSTEM_INFO, "Version": "10.0"}

# ------------------------------------------------------------------- /api/playlists
PLAYLISTS = ["Christmas 2025", "New Years 2026", "Testing"]

# Some FPP builds return objects rather than bare strings.
PLAYLISTS_AS_OBJECTS = [{"name": n} for n in PLAYLISTS]

# -------------------------------------------------------------- /api/playlist/<name>
# Deliberately includes: a disabled entry (must be reported, not dropped, so
# the reconciler deactivates rather than silently losing categories and votes),
# and a media-only entry with no sequence (skipped — nothing to vote for).
PLAYLIST_CHRISTMAS = {
    "name": "Christmas 2025",
    "repeat": 1,
    "loopCount": 0,
    "mainPlaylist": [
        {"type": "both", "enabled": 1, "playOnce": 0,
         "sequenceName": "Wizards in Winter (Instrumental).fseq",
         "mediaName": "12 Wizards in Winter (Instrumental).mp3",
         "duration": 177.0},
        {"type": "both", "enabled": 1, "playOnce": 0,
         "sequenceName": "Zero.fseq",
         "mediaName": "07 Zero.mp3",
         "duration": 224.5},
        {"type": "both", "enabled": 0, "playOnce": 0,
         "sequenceName": "Barbie Girl.fseq",
         "mediaName": "03 Barbie Girl.mp3",
         "duration": 196.0},
        {"type": "media", "enabled": 1, "playOnce": 0,
         "mediaName": "announcement.mp3", "duration": 12.0},
    ],
}

PLAYLIST_EMPTY = {"name": "Testing", "mainPlaylist": []}

# -------------------------------------------------------------------------- MQTT
# Topic suffixes under falcon/player/<hostname>/
MQTT_MESSAGES = [
    ("playlist/name/status", "Christmas 2025"),
    ("playlist/sequence/status", "Wizards in Winter (Instrumental).fseq"),
    ("playlist/media/title", "Wizards in Winter"),
    ("playlist/media/artist", "Trans-Siberian Orchestra"),
    ("status", "playing"),
    ("playlist_details", '{"playlist": "Christmas 2025", '
                         '"secondsElapsed": 47, "secondsRemaining": 130}'),
]
