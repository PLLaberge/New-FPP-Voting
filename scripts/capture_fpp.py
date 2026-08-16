"""Capture real responses from a real FPP, so the contract tests mean something.

Run this on any machine that can reach the Pi:

    python3 scripts/capture_fpp.py --host 192.168.1.50

It writes tests/fixtures/captured/*.json. `pytest` picks them up automatically
and runs the parsing against what FPP actually said — which is the only check
that proves the field names in fpp/http.py are right. Everything the suite does
without these files is self-consistency, not verification.

Capture with the show PLAYING if you can. An idle FPP omits most of the fields
that matter, so an idle-only capture proves very little.

Commit what it writes. The files are small, they document the exact FPP release
they came from, and they are what makes the FPP 10 upgrade a five-second check
instead of an evening of guessing.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

import httpx                                                     # noqa: E402

from fppvote.fpp import (                                        # noqa: E402
    major_minor, parse_playlist, parse_status, untested_version_warning,
)

OUT = ROOT / "tests" / "fixtures" / "captured"


def get(client, base, path):
    response = client.get(f"{base}{path}")
    response.raise_for_status()
    return response.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True,
                    help="Pi address or hostname, e.g. 192.168.1.50 or fpp.local")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--playlists", nargs="*", default=None,
                    help="which playlists to capture (default: all)")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}" if args.port != 80 \
        else f"http://{args.host}"
    OUT.mkdir(parents=True, exist_ok=True)
    written = []

    with httpx.Client(timeout=args.timeout) as client:
        try:
            info = get(client, base, "/api/system/info")
        except Exception as exc:                                 # noqa: BLE001
            sys.exit(f"Could not reach FPP at {base}: {exc}\n"
                     f"Check the address, and that the Pi is on.")

        version = str(info.get("Version") or info.get("version") or "unknown")
        tag = major_minor(version) or "unknown"
        print(f"FPP {version} on {info.get('Platform', '?')} "
              f"({info.get('HostName', '?')})")

        warning = untested_version_warning(version)
        print(f"  ! {warning}" if warning else
              f"  version {tag} is in TESTED_FPP_VERSIONS")

        def save(name, payload):
            path = OUT / f"{name}_{tag}.json"
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
            written.append(path)
            return path

        save("system_info", info)

        status = get(client, base, "/api/fppd/status")
        save("fppd_status", status)
        parsed = parse_status(status)
        print(f"\n  status parsed as: {parsed.status}")
        if parsed.is_playing:
            print(f"    playlist : {parsed.playlist_name}")
            print(f"    sequence : {parsed.sequence_name}")
            print(f"    timing   : {parsed.seconds_elapsed:.0f}s elapsed, "
                  f"{parsed.seconds_remaining:.0f}s remaining")
        else:
            print("    ! FPP is not playing. Capture again during a show — an "
                  "idle\n      status omits most of the fields that matter.")

        names = get(client, base, "/api/playlists")
        names = [n if isinstance(n, str) else (n.get("name") or n.get("playlist"))
                 for n in names or []]
        names = [n for n in names if n]
        save("playlists", names)
        print(f"\n  {len(names)} playlist(s): {', '.join(names) or '(none)'}")

        wanted = args.playlists if args.playlists is not None else names
        for name in wanted:
            if name not in names:
                print(f"    ! no such playlist: {name!r}")
                continue
            try:
                payload = get(client, base,
                              f"/api/playlist/{quote(name, safe='')}")
            except Exception as exc:                             # noqa: BLE001
                print(f"    ! could not read {name!r}: {exc}")
                continue
            slug = "".join(c if c.isalnum() else "-" for c in name).strip("-").lower()
            save(f"playlist_{slug}", payload)
            entries = parse_playlist(payload)
            disabled = sum(1 for e in entries if not e.enabled)
            print(f"    {name}: {len(entries)} entries parsed"
                  + (f", {disabled} disabled" if disabled else ""))
            if not entries:
                print("      ! parsed to nothing — the field names have moved. "
                      "This is\n        exactly what the capture is for; check "
                      "fpp/http.py against\n        the saved JSON.")

    print(f"\nWrote {len(written)} file(s) to {OUT.relative_to(ROOT)}/")
    for path in written:
        print(f"   {path.name}")
    print("\nNow run: pytest tests/test_adapter.py\n"
          "Then commit these files — they are what makes the next FPP upgrade "
          "a five-second check.")


if __name__ == "__main__":
    main()
