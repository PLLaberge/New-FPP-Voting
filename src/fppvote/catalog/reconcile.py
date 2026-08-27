"""
reconcile.py — playlist membership, additive and idempotent.

The parser reads the FPP playlist. It can tell you a song EXISTS; it can never
tell you a song is "Traditional" — that is editorial judgement, and since
2026-08-26 it lives on the song itself (`songs.categories`, global, not per
show — see CLAUDE.md), set once by hand on the admin page. This module no
longer touches categories at all; it only tracks whether a song is currently
part of a given show's playlist.

Reconciliation is additive and idempotent:

    parser output  +  what's already a member  =  new membership state

Rules
-----
1. A song that leaves the playlist is marked inactive, not deleted. Its
   categories (on `songs`, untouched by this module) and its vote history
   survive, so putting it back next season restores everything.
2. A song nobody has categorised yet still appears to voters under "All" —
   enforced downstream (Store.voteable_catalog), not here. A curation gap
   never hides a song from the people voting.
"""
from dataclasses import dataclass, field


@dataclass
class Membership:
    show_id: str
    key: str
    active: bool = True
    playlist_index: int = 0


@dataclass
class Report:
    added: list = field(default_factory=list)
    reactivated: list = field(default_factory=list)
    deactivated: list = field(default_factory=list)
    unchanged: int = 0
    needs_review: list = field(default_factory=list)   # filled by Store.sync_show

    def summary(self):
        return (f"+{len(self.added)} added, {len(self.reactivated)} reactivated, "
                f"-{len(self.deactivated)} deactivated, {self.unchanged} unchanged, "
                f"{len(self.needs_review)} need review")


def reconcile(show_id, parsed_rows, store):
    """store: {(show_id, key): Membership} — stands in for the show_songs table.
    Mutates store in place and returns a Report.

    needs_review is left empty here — this module has no view into
    `songs.categories` any more (deliberately: categories are global and pure
    membership tracking should not need to know about them). Store.sync_show
    fills that field in after calling this, by checking which of the rows it
    just wrote actually have categories set.
    """
    rep = Report()
    seen = set()

    for row in parsed_rows:
        key = row["key"]
        seen.add(key)
        existing = store.get((show_id, key))

        if existing:
            existing.playlist_index = row["playlist_index"]
            if not existing.active:
                existing.active = True
                rep.reactivated.append(key)          # votes intact
            else:
                rep.unchanged += 1
            continue

        store[(show_id, key)] = Membership(
            show_id, key, playlist_index=row["playlist_index"],
        )
        rep.added.append(key)

    for (sid, key), m in store.items():
        if sid == show_id and key not in seen and m.active:
            m.active = False                          # kept, not deleted
            rep.deactivated.append(key)
    return rep
