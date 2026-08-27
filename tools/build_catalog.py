"""Reconcile FPP playlists against curated metadata -> catalog.json + report."""
import json, sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parents[1])]
from tests.fixtures.playlists import CHRISTMAS, NYE
from fppvote.catalog.parser import parse_playlist
from fppvote.catalog.metadata import (META, CATEGORIES, SHOW_DEFS, SONG_CATEGORIES,
                                      PREVIOUSLY_LISTED_NOT_IN_PLAYLIST)

# Show copy comes from SHOW_DEFS so this script and init_db.py cannot drift.
# Categories are global now (2026-08-26, see CLAUDE.md) -- CATEGORIES/
# SONG_CATEGORIES, not one vocabulary per show -- only the playlist entries
# are joined in per show here.
PLAYLISTS = {"christmas": CHRISTMAS, "nye": NYE}

catalog, shows, report = {}, {}, []

for sid, entries in PLAYLISTS.items():
    cfg = SHOW_DEFS[sid]
    rows, issues = parse_playlist(entries)
    members = []
    for r in rows:
        key = r["key"]
        artist, year = META.get(key, (None, None))
        if r["feat"] and artist:
            artist = f"{artist} feat. {r['feat']}"
        elif r["feat"] and not artist:
            artist = f"feat. {r['feat']}"
        prev = catalog.get(key)
        if prev and prev["title"] != r["title"]:
            report.append(("conflict", sid, f"{key}: '{prev['title']}' vs '{r['title']}'"))
        cats = SONG_CATEGORIES.get(key)
        if cats is None:
            cats = []
            report.append(("needs_categories", sid, f"{key} ({r['title']})"))
        catalog[key] = {"title": r["title"], "artist": artist, "year": year,
                        "sequence": r["sequence"], "media": r["media"], "length": r["length"],
                        "categories": cats}
        members.append({"key": key, "categories": cats})
        if artist is None:
            report.append(("needs_artist", sid, f"{key} ({r['title']})"))
        if year is None:
            report.append(("needs_year", sid, f"{key} ({r['title']})"))
    shows[sid] = {"name": cfg["name"], "tagline": cfg["tagline"], "note": cfg["note"],
                 "songs": members}
    for i in issues:
        report.append((i["type"], sid, i["detail"]))
    for t in PREVIOUSLY_LISTED_NOT_IN_PLAYLIST.get(sid, []):
        report.append(("in_list_not_in_playlist", sid, t))

_h = SHOW_DEFS["halloween"]
shows["halloween"] = {"name": _h["name"], "tagline": _h["tagline"],
                      "note": _h["note"], "songs": []}

OUT = Path(__file__).resolve().parents[1] / "data" / "catalog.json"
json.dump({"songs": catalog, "categories": CATEGORIES, "shows": shows},
         open(OUT, "w"), indent=1, ensure_ascii=False)

xk = {m["key"] for m in shows["christmas"]["songs"]}
nk = {m["key"] for m in shows["nye"]["songs"]}
print(f"data/catalog.json written: {len(catalog)} unique songs across shows")
print(f"  Christmas {len(xk)}  |  New Year's {len(nk)}  |  shared {len(xk & nk)}  |  union {len(xk | nk)}\n")

order = ["duplicate_entry","shared_media","ambiguous_title","conflict",
         "in_list_not_in_playlist","needs_categories","needs_artist","needs_year","parse_note"]
for t in order:
    hits=[r for r in report if r[0]==t]
    if hits:
        print(f"[{t}] {len(hits)}")
        for _, sid, d in hits: print(f"   {sid:<10} {d}")
        print()
