"""
reconcile.py — where categories actually come from.

The parser reads the FPP playlist. It can tell you a song EXISTS; it can never
tell you a song is "Traditional" — that is editorial judgement and lives in the
database, set once by hand.

Reconciliation is therefore additive and idempotent:

    parser output  +  what's already curated  =  new state

Rules
-----
1. Categories already stored for (show, song) are NEVER overwritten by a re-run.
   Running reconcile 100 times changes nothing after the first.
2. A song that leaves the playlist is marked inactive, not deleted. Its
   categories and its vote history survive, so putting it back next season
   restores everything.
3. A song new to THIS show but already categorised in ANOTHER show gets those
   categories proposed (mapped through CATEGORY_ALIASES), marked `suggested`.
   Suggestions are applied but flagged, so you can accept or fix them.
4. A song nobody has ever categorised lands in `needs_review` with zero
   categories — and still appears to voters under "All". Curation gaps never
   hide a song from the people voting.
"""
from dataclasses import dataclass, field

# Same idea in two shows under different names. Used only to seed suggestions.
CATEGORY_ALIASES = {
    ("christmas", "nye"): {
        "Rock & Roll": "Rock",
        "Contemporary": "Pop",
        "Kids & Movies": "Kids & Movies",
        "New this year": "New this year",
        "Not-So-Christmasy": "Pop",
    },
    ("nye", "christmas"): {
        "Rock": "Rock & Roll",
        "Pop": "Contemporary",
        "Kids & Movies": "Kids & Movies",
        "New this year": "New this year",
        "Dance Tunes": "Contemporary",
        "Instrumental": "Contemporary",
        "Throwback": "Crooners",
        "Countdown": "Contemporary",
    },
}


@dataclass
class Membership:
    show_id: str
    key: str
    categories: list
    active: bool = True
    source: str = "curated"        # curated | suggested | needs_review
    playlist_index: int = 0


@dataclass
class Report:
    added: list = field(default_factory=list)
    reactivated: list = field(default_factory=list)
    deactivated: list = field(default_factory=list)
    unchanged: int = 0
    suggested: list = field(default_factory=list)
    needs_review: list = field(default_factory=list)

    def summary(self):
        return (f"+{len(self.added)} added, {len(self.reactivated)} reactivated, "
                f"-{len(self.deactivated)} deactivated, {self.unchanged} unchanged, "
                f"{len(self.suggested)} suggested, {len(self.needs_review)} need review")


def suggest_from_other_shows(key, show_id, store, valid):
    """Propose categories for a song already curated in a different show."""
    for (sid, k), m in store.items():
        if k != key or sid == show_id or not m.categories:
            continue
        table = CATEGORY_ALIASES.get((sid, show_id), {})
        mapped = []
        for c in m.categories:
            t = table.get(c)
            if t and t in valid and t not in mapped:
                mapped.append(t)
        if mapped:
            return mapped, sid
    return [], None


def reconcile(show_id, parsed_rows, store, valid_categories):
    """store: {(show_id, key): Membership} — stands in for the show_songs table.
    Mutates store in place and returns a Report."""
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
                rep.reactivated.append(key)          # categories & votes intact
            else:
                rep.unchanged += 1
            if not existing.categories:
                rep.needs_review.append(key)
            continue

        cats, borrowed = suggest_from_other_shows(key, show_id, store, valid_categories)
        store[(show_id, key)] = Membership(
            show_id, key, cats,
            source="suggested" if cats else "needs_review",
            playlist_index=row["playlist_index"],
        )
        rep.added.append(key)
        if cats:
            rep.suggested.append((key, cats, borrowed))
        else:
            rep.needs_review.append(key)

    for (sid, key), m in store.items():
        if sid == show_id and key not in seen and m.active:
            m.active = False                          # kept, not deleted
            rep.deactivated.append(key)
    return rep
