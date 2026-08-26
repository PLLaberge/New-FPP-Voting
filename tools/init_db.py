"""Create or update the voting database from the playlists and curated metadata.

Safe to run repeatedly — that is the point. Every write in the store is
additive, so a re-run picks up playlist changes without touching anything a
human has since edited on the admin page.

    python3 tools/init_db.py [--db data/fppvote.db] [--recategorise]

By default categories are seeded only for songs that have none, so your
curation wins. --recategorise reapplies metadata.py over the top, which is what
you want after editing that file by hand and nothing else.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.fixtures.playlists import CHRISTMAS, NYE          # noqa: E402
from fppvote.catalog.metadata import (                       # noqa: E402
    CHRISTMAS_CATS, META, NYE_CATS, SHOW_DEFS,
)
from fppvote.catalog.parser import parse_playlist            # noqa: E402
from fppvote.db import Store                                 # noqa: E402

PLAYLISTS = {"christmas": (CHRISTMAS, CHRISTMAS_CATS), "nye": (NYE, NYE_CATS)}


def artist_for(key, row):
    """Compose the artist the same way build_catalog.py does: a (feat. X) in
    the filename belongs in the artist field, not the title."""
    artist, year = META.get(key, (None, None))
    if row["feat"]:
        artist = f"{artist} feat. {row['feat']}" if artist else f"feat. {row['feat']}"
    return artist, year


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=ROOT / "data" / "fppvote.db", type=Path)
    ap.add_argument("--recategorise", action="store_true",
                    help="reapply metadata.py categories over existing ones")
    args = ap.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.db.exists()
    store = Store.open(args.db)
    print(f"{'created' if fresh else 'opened'} {args.db}")
    # Global since 2026-08-25 (see CLAUDE.md) -- one allowance and one
    # cooldown for the whole install, not per show, so this line moved out of
    # the per-show loop below.
    print(f"voting rules (global): {store.votes_per_round()} vote(s)/round, "
          f"cooldown {store.cooldown_songs()}\n")

    skipped = []

    for show_id, cfg in SHOW_DEFS.items():
        before = store.get_show(show_id)
        outcome = store.define_show(
            show_id, cfg["name"], cfg["playlist_name"],
            tagline=cfg["tagline"], note=cfg["note"], theme=cfg["theme"],
        )
        orphaned = store.set_show_categories(show_id, cfg["categories"])
        show = store.get_show(show_id)
        print(f"[{show_id}] {show.name} — {outcome}"
              f", {len(cfg['categories'])} categories")
        print(f"    playlist: {show.playlist_name!r}")
        if before and before.playlist_name != show.playlist_name:
            # The one field where a silent no-op would cost an evening of
            # debugging, so it gets called out rather than merely applied.
            print(f"    ^ changed from {before.playlist_name!r}")
        if orphaned:
            print(f"    ! songs still assigned to removed categories: {orphaned}")

        if show_id not in PLAYLISTS:
            print("    no playlist yet — nothing to sync\n")
            continue

        entries, curated = PLAYLISTS[show_id]
        rows, issues = parse_playlist(entries)
        metadata = {r["key"]: artist_for(r["key"], r) for r in rows}
        report = store.sync_show(show_id, rows, metadata=metadata)
        print(f"    sync: {report.summary()}")

        # Categories are editorial. metadata.py IS the human's curation, so it
        # outranks a cross-show `suggested` guess and fills a `needs_review`
        # gap — but never overwrites something already marked curated, which
        # means it came from the admin page and is newer than this file.
        valid = set(cfg["categories"])
        existing = {s.key: s for s in
                    store.list_show_songs(show_id, include_inactive=True)}
        applied = 0
        for key, cats in curated.items():
            if key not in existing:
                continue
            if existing[key].source == "curated" and not args.recategorise:
                continue
            usable = [c for c in cats if c in valid]
            for c in cats:
                if c not in valid:
                    skipped.append((show_id, key, c))
            if usable:
                store.set_categories(show_id, key, usable)
                applied += 1
        print(f"    categories: {applied} song(s) written"
              f"{' (--recategorise)' if args.recategorise else ' from metadata.py'}")

        if issues:
            kinds = {}
            for i in issues:
                kinds[i["type"]] = kinds.get(i["type"], 0) + 1
            print("    parser notes: "
                  + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))

        songs = store.list_show_songs(show_id)
        review = [s for s in songs if s.needs_review]
        print(f"    {len(songs)} active songs, {len(review)} still uncategorised"
              + (f" (still votable under All)" if review else "") + "\n")

    if skipped:
        print("Category assignments refused — no such chip in that show:")
        for show_id, key, cat in skipped:
            print(f"    {show_id:<10} {key:<34} {cat!r}")
        print("    Fix by adding the category to SHOW_DEFS or changing the "
              "assignment in metadata.py.\n")

    placeholders = [s for s in SHOW_DEFS
                    if s not in PLAYLISTS and store.get_show(s)]
    if placeholders:
        print("Placeholder playlist name(s), for shows whose playlist does not "
              "exist yet:")
        for show_id in placeholders:
            print(f"    {show_id:<10} {store.get_show(show_id).playlist_name!r}")
        print("    Set the real name in SHOW_DEFS when the playlist exists; "
              "re-running\n    this script applies it.\n")

    print("FPP keeps playlists in ~/media/playlists/<name>.json and refers to "
          "them\nwithout the suffix. Matching tolerates the suffix and case "
          "either way.")
    store.close()


if __name__ == "__main__":
    main()
