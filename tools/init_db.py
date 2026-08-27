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
    CATEGORIES, META, SHOW_DEFS, SONG_CATEGORIES,
)
from fppvote.catalog.parser import parse_playlist            # noqa: E402
from fppvote.db import Store                                 # noqa: E402

PLAYLISTS = {"christmas": CHRISTMAS, "nye": NYE}


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
    # cooldown for the whole install, not per show.
    print(f"voting rules (global): {store.votes_per_round()} vote(s)/round, "
          f"cooldown {store.cooldown_songs()}")

    # Categories are global too, since 2026-08-26 -- one vocabulary for the
    # whole install, seeded once here rather than per show.
    orphaned = store.set_category_vocabulary(CATEGORIES)
    print(f"categories (global): {len(CATEGORIES)} in the vocabulary")
    if orphaned:
        print(f"    ! songs still assigned to removed categories: {orphaned}")
    print()

    skipped = []

    for show_id, cfg in SHOW_DEFS.items():
        before = store.get_show(show_id)
        outcome = store.define_show(
            show_id, cfg["name"], cfg["playlist_name"],
            tagline=cfg["tagline"], note=cfg["note"], theme=cfg["theme"],
        )
        show = store.get_show(show_id)
        print(f"[{show_id}] {show.name} — {outcome}")
        print(f"    playlist: {show.playlist_name!r}")
        if before and before.playlist_name != show.playlist_name:
            # The one field where a silent no-op would cost an evening of
            # debugging, so it gets called out rather than merely applied.
            print(f"    ^ changed from {before.playlist_name!r}")

        if show_id not in PLAYLISTS:
            print("    no playlist yet — nothing to sync\n")
            continue

        entries = PLAYLISTS[show_id]
        rows, issues = parse_playlist(entries)
        metadata = {r["key"]: artist_for(r["key"], r) for r in rows}
        report = store.sync_show(show_id, rows, metadata=metadata)
        print(f"    sync: {report.summary()}")

        if issues:
            kinds = {}
            for i in issues:
                kinds[i["type"]] = kinds.get(i["type"], 0) + 1
            print("    parser notes: "
                  + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))
        print()

    # Categories are editorial and global (2026-08-26) — one pass over
    # SONG_CATEGORIES, not per show, and deliberately run AFTER every show has
    # synced above: the "still uncategorised" counts printed next need to see
    # the post-categorisation state, not a snapshot from partway through this
    # run. metadata.py IS the human's curation, so it fills a needs_review gap
    # but never overwrites a song that already has categories set, which means
    # it came from the admin page (or an earlier run of this script) and
    # outranks this file.
    valid = set(CATEGORIES)
    applied = 0
    for key, cats in SONG_CATEGORIES.items():
        song = store.get_song(key)
        if song is None:
            continue          # not in the catalogue at all (yet)
        if song.categories and not args.recategorise:
            continue
        usable = [c for c in cats if c in valid]
        for c in cats:
            if c not in valid:
                skipped.append((key, c))
        if usable:
            store.set_categories(key, usable)
            applied += 1
    print(f"categories: {applied} song(s) written"
          f"{' (--recategorise)' if args.recategorise else ' from metadata.py'}\n")

    for show_id in PLAYLISTS:
        if store.get_show(show_id) is None:
            continue
        songs = store.list_show_songs(show_id)
        review = [s for s in songs if s.needs_review]
        print(f"[{show_id}] {len(songs)} active songs, {len(review)} still uncategorised"
              + (" (still votable under All)" if review else ""))
    print()

    if skipped:
        print("Category assignments refused — no such chip in the vocabulary:")
        for key, cat in skipped:
            print(f"    {key:<34} {cat!r}")
        print("    Fix by adding the category to CATEGORIES or changing the "
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
