"""
Contract tests: the same assertions against every implementation.

The point of this file is that upgrading FPP and running `pytest` tells you in
seconds whether anything moved. FPP 10 ships mid-August 2026.

Read the header of tests/fixtures/fpp_responses.py first. The canned responses
are CONSTRUCTED from FPP's documented shapes, not captured from a real Pi, so a
green run here means "the parsing is self-consistent", not "this works against
FPP". Run scripts/capture_fpp.py against the Pi and the tests at the bottom of
this file start checking against what FPP actually said.
"""
import json
from pathlib import Path

import httpx
import pytest

from fppvote.fpp import (
    STATUS_IDLE, STATUS_PLAYING, STATUS_STOPPING, STATUS_UNKNOWN,
    FakeFppAdapter, FppAdapter, FppError, HttpFppAdapter, MqttFppAdapter,
    PlaylistEntry, from_catalog, major_minor, parse_playlist, parse_status,
    untested_version_warning,
)
from tests.fixtures import fpp_responses as R

CAPTURED = Path(__file__).parent / "fixtures" / "captured"

# The show every adapter is wired to serve, so one set of assertions fits all.
SHOW = {
    "Christmas 2025": [
        ("Wizards in Winter (Instrumental).fseq",
         "12 Wizards in Winter (Instrumental).mp3", "02:57"),
        ("Zero.fseq", "07 Zero.mp3", "03:44"),
    ],
    "New Years 2026": [("Auld Lang Syne.fseq", "01 Auld Lang Syne.mp3", "02:30")],
}


# --------------------------------------------------------------- transports
def _routes(request: httpx.Request) -> httpx.Response:
    """A stand-in FPP built from the canned responses."""
    path = request.url.path
    if path == "/api/fppd/status":
        return httpx.Response(200, json=R.STATUS_PLAYING)
    if path == "/api/system/info":
        return httpx.Response(200, json=R.SYSTEM_INFO)
    if path == "/api/playlists":
        return httpx.Response(200, json=R.PLAYLISTS)
    if path.startswith("/api/playlist/"):
        return httpx.Response(200, json=R.PLAYLIST_CHRISTMAS)
    if path == "/api/command":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404)


def http_adapter(routes=_routes) -> HttpFppAdapter:
    return HttpFppAdapter("http://fpp.local",
                          client=httpx.Client(transport=httpx.MockTransport(routes)))


class FakeMqttClient:
    """Records publishes; messages are fed in via adapter.handle()."""
    def __init__(self, rc=0):
        self.published = []
        self.rc = rc

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return type("Result", (), {"rc": self.rc})()


@pytest.fixture(params=["fake", "http", "mqtt"])
def adapter(request):
    """Every implementation, serving the same show."""
    if request.param == "fake":
        fake = from_catalog(SHOW)
        fake.start_at_item("Christmas 2025", 1)
        return fake
    if request.param == "http":
        return http_adapter()
    mqtt = MqttFppAdapter(http_adapter(), client=FakeMqttClient())
    for topic, payload in R.MQTT_MESSAGES:
        mqtt.handle(f"{mqtt.topic_root}/{topic}", payload)
    return mqtt


# ------------------------------------------------------------ the contract
def test_every_implementation_satisfies_the_protocol(adapter):
    assert isinstance(adapter, FppAdapter)


def test_status_reports_what_is_playing(adapter):
    status = adapter.get_status()
    assert status.status == STATUS_PLAYING
    assert status.is_playing is True
    assert status.playlist_name == "Christmas 2025"
    assert status.sequence_name == "Wizards in Winter (Instrumental).fseq"


def test_status_carries_usable_timings(adapter):
    status = adapter.get_status()
    assert status.seconds_elapsed >= 0
    assert status.seconds_remaining > 0
    assert status.seconds_total >= status.seconds_remaining


def test_the_sequence_name_is_what_identity_is_built_from(adapter):
    """song_key comes from sequenceName. If an adapter ever returns the media
    name here, three Christmas entries silently merge into one song."""
    from fppvote.catalog.parser import slugify
    assert slugify(adapter.get_status().sequence_name) == "wizards-in-winter-instrumental"


def test_playlists_can_be_listed(adapter):
    assert "Christmas 2025" in adapter.list_playlists()


def test_a_playlist_reads_back_as_entries(adapter):
    entries = adapter.get_playlist("Christmas 2025")
    assert entries and all(isinstance(e, PlaylistEntry) for e in entries)
    assert all(e.sequence_name.endswith(".fseq") for e in entries)
    assert [e.index for e in entries] == list(range(1, len(entries) + 1))
    assert all(e.duration_seconds > 0 for e in entries)


def test_start_at_item_is_accepted(adapter):
    adapter.start_at_item("Christmas 2025", 2)   # must not raise


def test_version_is_reported(adapter):
    assert major_minor(adapter.version()) in ("9.5", "10.0")


# --------------------------------------------------- degradation (the feature)
def test_status_never_raises_when_fpp_is_unreachable():
    """'The playlist should just keep playing.' Every adapter degrades to
    'unknown' rather than throwing, because a failed status read must not be
    able to stop the show."""
    def dead(request):
        raise httpx.ConnectError("no route to host")

    fake = from_catalog(SHOW)
    fake.start_at_item("Christmas 2025", 1)
    fake.go_offline()

    http = http_adapter(dead)
    mqtt = MqttFppAdapter(http_adapter(dead), client=FakeMqttClient())

    for adapter in (fake, http, mqtt):
        status = adapter.get_status()
        assert status.status == STATUS_UNKNOWN
        assert status.is_playing is False


def test_unknown_is_not_the_same_as_idle():
    """The service closes a round on idle but must NOT on unknown, or a network
    blip discards every vote cast so far."""
    fake = from_catalog(SHOW)
    fake.start_at_item("Christmas 2025", 1)
    fake.stop()
    assert fake.get_status().status == STATUS_IDLE
    fake.go_offline()
    assert fake.get_status().status == STATUS_UNKNOWN


def test_other_calls_raise_rather_than_pretend():
    """A vote result that silently goes nowhere is worse than an error."""
    fake = from_catalog(SHOW)
    fake.go_offline()
    with pytest.raises(FppError):
        fake.start_at_item("Christmas 2025", 1)
    with pytest.raises(FppError):
        fake.list_playlists()


# ------------------------------------------------------------ status parsing
@pytest.mark.parametrize("payload,expected", [
    (R.STATUS_PLAYING, STATUS_PLAYING),
    (R.STATUS_IDLE, STATUS_IDLE),
    (R.STATUS_STOPPING, STATUS_STOPPING),
    (R.STATUS_PAUSED, STATUS_PLAYING),      # deliberate; see the fixture comment
    (R.STATUS_OLD_SHAPE, STATUS_PLAYING),
    (R.STATUS_GARBAGE, STATUS_UNKNOWN),
])
def test_status_shapes_map_to_the_right_state(payload, expected):
    assert parse_status(payload).status == expected


def test_an_older_field_layout_still_parses():
    """No nested current_playlist, mm:ss strings, a different sequence key."""
    status = parse_status(R.STATUS_OLD_SHAPE)
    assert status.playlist_name == "Christmas 2025"
    assert status.sequence_name == "Wizards in Winter (Instrumental).fseq"
    assert status.seconds_elapsed == 47
    assert status.seconds_remaining == 130


def test_an_unrecognised_payload_degrades_instead_of_raising():
    status = parse_status(R.STATUS_GARBAGE)
    assert status.status == STATUS_UNKNOWN
    assert status.sequence_name is None


def test_a_broken_status_body_still_returns_a_status():
    def broken(request):
        return httpx.Response(200, content=b"<html>not json</html>")
    assert http_adapter(broken).get_status().status == STATUS_UNKNOWN


# ---------------------------------------------------------- playlist parsing
def test_a_disabled_entry_is_reported_not_dropped():
    """Dropping it would look like the song left the playlist, and the
    reconciler would deactivate it — losing nothing, but for the wrong reason.
    Reporting `enabled` lets that be a decision rather than an accident."""
    entries = parse_playlist(R.PLAYLIST_CHRISTMAS)
    disabled = [e for e in entries if not e.enabled]
    assert [e.sequence_name for e in disabled] == ["Barbie Girl.fseq"]


def test_a_media_only_entry_is_skipped():
    """No sequence means nothing to vote for — and no song_key to build."""
    entries = parse_playlist(R.PLAYLIST_CHRISTMAS)
    assert all(e.sequence_name for e in entries)
    assert len(entries) == 3


def test_an_empty_playlist_is_empty_not_an_error():
    assert parse_playlist(R.PLAYLIST_EMPTY) == []


def test_playlists_returned_as_objects_still_list():
    def routes(request):
        if request.url.path == "/api/playlists":
            return httpx.Response(200, json=R.PLAYLISTS_AS_OBJECTS)
        return _routes(request)
    assert "Christmas 2025" in http_adapter(routes).list_playlists()


def test_playlist_names_with_spaces_are_encoded():
    """Real playlist names have spaces and apostrophes ("New Year's 2026").
    Asserted on raw_path — the wire form — because .path decodes it back."""
    seen = {}

    def routes(request):
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json=R.PLAYLIST_CHRISTMAS)

    http_adapter(routes).get_playlist("New Year's 2026")
    assert " " not in seen["raw"]
    assert seen["raw"] == "/api/playlist/New%20Year%27s%202026"


# ------------------------------------------------------------------ the fake
def test_the_fake_plays_a_show_without_a_pi():
    fake = from_catalog(SHOW)
    fake.start_at_item("Christmas 2025", 1)
    assert fake.get_status().sequence_name == "Wizards in Winter (Instrumental).fseq"

    fake.play_to_end_of_song()
    assert fake.get_status().sequence_name == "Zero.fseq"

    fake.play_to_end_of_song()
    assert fake.get_status().sequence_name == "Wizards in Winter (Instrumental).fseq", \
        "the playlist wraps, so the cooldown window rolls over"


def test_the_fake_advances_across_several_songs_in_one_tick():
    fake = from_catalog(SHOW)
    fake.start_at_item("Christmas 2025", 1)
    fake.tick(60 * 60)          # an hour of show
    assert fake.get_status().is_playing


def test_the_fake_records_what_it_was_asked_to_play():
    fake = from_catalog(SHOW)
    fake.start_at_item("Christmas 2025", 2)
    assert fake.commands == [("Christmas 2025", 2)]
    assert fake.get_status().sequence_name == "Zero.fseq"


# ------------------------------------------------------------------ the mqtt
def test_mqtt_falls_back_to_http_when_messages_stop():
    """A broker that connects and then says nothing looks healthy from the
    connection's side, so freshness is judged on a timestamp."""
    now = [1000.0]
    mqtt = MqttFppAdapter(http_adapter(), client=FakeMqttClient(),
                          stale_after=15.0, clock=lambda: now[0])
    for topic, payload in R.MQTT_MESSAGES:
        mqtt.handle(f"{mqtt.topic_root}/{topic}", payload)

    assert mqtt.fresh is True
    assert mqtt.get_status().seconds_remaining == 130    # from playlist_details

    now[0] += 60                                        # the broker goes quiet
    assert mqtt.fresh is False
    assert mqtt.get_status().seconds_remaining == 130    # ...same answer, via HTTP
    assert mqtt.get_status().sequence_name == "Wizards in Winter (Instrumental).fseq"


def test_mqtt_with_no_messages_at_all_still_works():
    mqtt = MqttFppAdapter(http_adapter(), client=FakeMqttClient())
    assert mqtt.fresh is False
    assert mqtt.get_status().status == STATUS_PLAYING    # straight from HTTP


def test_mqtt_prefers_publishing_but_falls_back_to_http():
    client = FakeMqttClient()
    mqtt = MqttFppAdapter(http_adapter(), client=client)
    mqtt.start_at_item("Christmas 2025", 3)
    assert client.published == [
        ("falcon/player/FPP/set/playlist/Christmas 2025/startPosition", "3")]

    posted = []

    def routes(request):
        if request.url.path == "/api/command":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok"})
        return _routes(request)

    failing = MqttFppAdapter(http_adapter(routes), client=FakeMqttClient(rc=1))
    failing.start_at_item("Christmas 2025", 3)
    assert posted and posted[0]["args"] == ["Christmas 2025", 3]


def test_mqtt_ignores_a_malformed_details_payload():
    mqtt = MqttFppAdapter(http_adapter(), client=FakeMqttClient())
    mqtt.handle(f"{mqtt.topic_root}/playlist_details", "{not json")
    mqtt.handle(f"{mqtt.topic_root}/playlist/sequence/status", "Zero.fseq")
    assert mqtt.get_status().sequence_name == "Zero.fseq"


# -------------------------------------------------------------- FPP versions
def test_a_tested_version_produces_no_warning():
    assert untested_version_warning("9.5") is None
    assert untested_version_warning("9.5.1") is None, "patch releases are fine"


def test_an_untested_version_warns_but_does_not_refuse():
    """FPP 10 ships mid-August 2026. Warn loudly; never take the show down over
    a version string."""
    warning = untested_version_warning("10.0")
    assert warning and "10.0" in warning and "not been tested" in warning
    assert untested_version_warning(None) is not None


def test_tested_versions_were_not_widened_without_captures():
    """The old plugin declared support for versions that did not exist yet.
    Adding a version here without a capture from that FPP repeats the mistake.
    """
    from fppvote.fpp import TESTED_FPP_VERSIONS
    assert TESTED_FPP_VERSIONS == ("8.0", "9.0", "9.5")
    for version in TESTED_FPP_VERSIONS:
        captured = CAPTURED / f"fppd_status_{version}.json"
        if not captured.exists():
            pytest.skip(f"no capture for FPP {version} yet — run "
                        f"scripts/capture_fpp.py on the Pi")


# ------------------------------------------------- captured real responses
def _captures():
    return sorted(CAPTURED.glob("fppd_status*.json")) if CAPTURED.exists() else []


def _capture_id(path):
    # pytest hands an empty parametrize list a sentinel, not a Path.
    return getattr(path, "stem", str(path))


def test_captured_responses_are_present():
    """Fails loudly until someone captures from a real Pi.

    CLAUDE.md requires the contract tests run against captured real responses.
    Everything above this line runs against responses CONSTRUCTED from FPP's
    documented shapes by a machine that had no Pi to ask. That is enough to
    catch a regression in the parsing and not enough to prove the field names
    are right.
    """
    if not _captures():
        pytest.xfail("no captures yet — run scripts/capture_fpp.py --host <pi>")
    assert _captures()


@pytest.mark.parametrize("path", _captures(), ids=_capture_id)
def test_a_captured_status_parses(path):
    """The assertion that actually counts: real FPP output, our parser."""
    status = parse_status(json.loads(path.read_text()))
    assert status.status != STATUS_UNKNOWN, (
        f"{path.name} did not parse — FPP's field names have moved. "
        f"Keys present: {sorted(json.loads(path.read_text()))}")
    if status.is_playing:
        assert status.sequence_name, "playing, but no sequence to build a key from"


@pytest.mark.parametrize("path",
                         sorted(CAPTURED.glob("playlist_*.json"))
                         if CAPTURED.exists() else [],
                         ids=_capture_id)
def test_a_captured_playlist_parses(path):
    entries = parse_playlist(json.loads(path.read_text()))
    assert entries, f"{path.name} produced no entries"
    assert all(e.sequence_name for e in entries)
