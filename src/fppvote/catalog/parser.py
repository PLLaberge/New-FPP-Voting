"""
fpp_catalog.py — turn FPP playlist entries into catalog rows.

Identity rule
-------------
song_key is derived from sequenceName (the .fseq), NOT mediaName and NOT the
playlist index.

  * playlist index  -> shifts whenever a song is added. This is the bug in the
                       current plugin.
  * mediaName       -> not unique. Three Christmas entries share
                       "01 - Santa Won't You (Bring Me Love).mp3".
  * sequenceName    -> one per light-show sequence, which is what a viewer is
                       actually voting for.

Display rule (per Paulin)
-------------------------
Title comes from mediaName, shortened to the common name:
  - drop leading track numbers ("01 ", "01-04- ", "205 - ", "3-15 ")
  - drop qualifiers: (Instrumental), (Single), with Intro, Official Disney HD,
    Sing-along, final, -short, -edited-V3, (Edit) ...
  - move (feat. X) into the artist field
  - fall back to sequenceName when mediaName is unusable (taylorshow2a.mp3)
"""
import re, unicodedata

AUDIO_EXT = re.compile(r"\.(mp3|m4a|mp4|wav|ogg|flac|aac)$", re.I)

# leading track numbers — deliberately conservative so "300 Violin Orchestra"
# survives (3 digits, no separator = probably part of the title)
TRACK_PATTERNS = [
    re.compile(r"^\d{1,2}-\d{1,2}-?\s+"),      # 01-04- , 3-15
    re.compile(r"^\d{1,3}\s*[-–]\s+"),         # 205 - , 01 -
    re.compile(r"^\d{1,2}\s+"),                # 01 , 07
]

FEAT = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s*([^)\]]*)[\)\]]?", re.I)

# qualifiers stripped from the display title
NOISE = [
    r"\(\s*instrumental\s*\)", r"\[\s*instrumental\s*\]",
    r"\(\s*single\s*\)?", r"\(\s*edit\s*\)", r"\(\s*radio version\s*\)",
    r"\(\s*from the original motion pi.*$",
    r"\bofficial disney hd\b", r"\bsing-?along\b",
    r"\bwith intro\s*\d*\b", r"\bfilm final\b", r"\bfinal\b",
    r"-short-short", r"-short\b", r"-edited-v\d*", r"-edited\b",
    r"\bv\d+\s*$", r"\bshort version\b",
]
NOISE_RE = [re.compile(p, re.I) for p in NOISE]

# Cases the generic rules can't reach. The real admin page exposes this as an
# editable "display name" override per song.
OVERRIDES = {
    "frozen-let-it-go-sing-along-official-disney-hd": "Let It Go",
    "frozen-2-into-the-unknown":                      "Into the Unknown",
    "cant-stop-the-feeling-film-final":               "Can't Stop the Feeling",
    "taylor-swift-show":                              "Taylor Swift Show",
    "star-wars-funk-final":                           "Star Wars Uptown Funk",
    "golden-kpop-demon-hunters":                      "Golden",
    "disney-medley-happily-ever-after":               "Happily Ever After",
    "danger-zone-top-gun":                            "Danger Zone",
    "christmas-sarajevo-12-24-instrumental":          "Christmas Eve / Sarajevo 12/24",
    "a-star-to-follow-short-short-xmas":              "A Star to Follow",
    "carol-of-the-bells-foster-instrumental":         "Carol of the Bells",
    "light-of-christmas-feat-tobymac":                "Light of Christmas",
    "toccata-and-fugue-paranormal-remix":             "Toccata and Fugue",
    "darude-sandstorm":                               "Sandstorm",
    "300-violin-orchestra":                           "300 Violin Orchestra",
    "star-wars-imperial-march-x-carol-of-the-bells":  "Star Wars: Imperial March x Carol of the Bells",
    "youre-a-mean-one-mr-grinch":                     "You're a Mean One, Mr. Grinch",
    "takin-care-of-christmas":                        "Takin' Care of Christmas",
    "how-far-ill-go":                                 "How Far I'll Go",
    "we-dont-talk-about-bruno":                       "We Don't Talk About Bruno",
    "let-it-snow-baby-its-cold-outside":              "Let It Snow, Baby; It's Cold Outside",
    "mele-kalikimaka":                                "Mele Kalikimaka",
    "the-spirit-of-christmas-medley":                 "The Spirit of Christmas Medley",
    "anti-hero":                                      "Anti-Hero (Kungs Remix)",
    # three different Carol of the Bells sequences — disambiguate on screen
    "carol-of-the-bells":                             "Carol of the Bells",
}

# artists that live inside the filename rather than a feat. tag
ARTIST_IN_NAME = {"golden-kpop-demon-hunters": "HUNTR/X", "darude-sandstorm": "Darude"}

# ---------------------------------------------------------------- truncation
# FPP truncates long media filenames mid-word. When the surviving fragment is
# an unambiguous prefix of something we recognise we repair it silently and say
# so; when it isn't, we leave it alone and flag it rather than guessing.
KNOWN_QUALIFIERS = [
    "Instrumental", "Single", "Edit", "Live", "Radio Version", "Epic Version",
    "Paranormal Remix", "Kungs Remix", "Merry Christmas", "Russian Dance",
    "The Little Mermaid", "Bring Me Love", "Short Version",
    "From the Original Motion Picture Soundtrack",
]
KNOWN_ARTISTS = [
    "Casey Abrams", "Reba McEntire", "Ken Darby", "tobyMac", "Snoopy",
]

def _complete(fragment, candidates):
    """Return the unique candidate this fragment prefixes, else None."""
    f = fragment.strip().rstrip(".").lower()
    if not f:
        return None
    hits = [c for c in candidates if c.lower().startswith(f)]
    return hits[0] if len(hits) == 1 else None


def repair_truncation(name):
    """name has an unmatched '('. Return (repaired_name, note)."""
    idx = name.rfind("(")
    head, frag = name[:idx].rstrip(), name[idx + 1:].strip()

    m = re.match(r"(?:feat\.?|ft\.?|featuring)\s*(.*)$", frag, re.I)
    if m:
        artist = _complete(m.group(1), KNOWN_ARTISTS)
        if artist:
            return f"{head} (feat. {artist})", f"auto-corrected truncated artist to '{artist}'"
        return f"{head} (feat. {m.group(1)})", f"truncated artist '{m.group(1)}' — please confirm"

    qual = _complete(frag, KNOWN_QUALIFIERS)
    if qual:
        return f"{head} ({qual})", f"auto-corrected truncated qualifier to '({qual})'"
    return head, f"dropped unrecognised truncated fragment '({frag}' — please confirm"

# small words stay lowercase unless they start the title
SMALL = {"a","an","and","as","at","but","by","for","in","of","on","or","the","to","x","vs"}

def titlecase_smallwords(t: str) -> str:
    """Fix filename capitalisation like 'Life Is A Highway' -> 'Life Is a Highway'
    without touching deliberate casing such as 'tobyMac' or 'KPop'."""
    parts = t.split(" ")
    out = []
    for i, w in enumerate(parts):
        bare = re.sub(r"[^A-Za-z]", "", w)
        # note: "".islower() is False, so single-letter words need the len guard
        if (i > 0 and bare.lower() in SMALL and bare[:1].isupper()
                and (len(bare) == 1 or bare[1:].islower())):
            out.append(w[0].lower() + w[1:])
        else:
            out.append(w)
    return " ".join(out)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\.(fseq)$", "", text, flags=re.I)
    text = re.sub(r"[’']", "", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def strip_track_number(name: str) -> str:
    for pat in TRACK_PATTERNS:
        new = pat.sub("", name, count=1)
        if new != name:
            return new.strip()
    return name.strip()


def clean_title(media: str, sequence: str):
    """Return (display_title, feat_artist, notes[])."""
    notes = []
    raw = AUDIO_EXT.sub("", (media or "")).strip()
    name = strip_track_number(raw)

    if name.count("(") > name.count(")"):
        name, note = repair_truncation(name)
        notes.append(note)

    # unusable = no letters at all, or a single all-lowercase blob like "taylorshow2a"
    usable = bool(re.search(r"[a-zA-Z]", name))
    if usable and " " not in name and (name.islower() or re.search(r"[a-z]\d", name)):
        usable = False
    if not usable:
        notes.append("media name unusable — fell back to sequence name")
        name = strip_track_number(re.sub(r"\.fseq$", "", sequence, flags=re.I))

    feat = None
    m = FEAT.search(name)
    if m:
        feat = m.group(1).strip().rstrip(".,") or None
        name = FEAT.sub("", name).strip()

    for rx in NOISE_RE:
        name = rx.sub("", name)

    name = re.sub(r"[\(\[]\s*$", "", name)          # dangling open bracket
    name = re.sub(r"\s*[-–]\s*$", "", name)         # trailing dash
    name = re.sub(r"\s{2,}", " ", name).strip(" -–[(")

    name = titlecase_smallwords(name)
    name = re.sub(r"\bIts\b", "It's", name)
    name = re.sub(r"\bDont\b", "Don't", name)
    name = re.sub(r"\bWont\b", "Won't", name)

    key = slugify(sequence)
    if key in OVERRIDES:
        name = OVERRIDES[key]
    return name, feat, notes


def parse_entry(sequence: str, media: str, length: str, index: int):
    title, feat, notes = clean_title(media, sequence)
    key = slugify(sequence)
    return {
        "key": key,
        "title": title,
        "feat": feat,
        "artist_hint": ARTIST_IN_NAME.get(key),
        "sequence": sequence,
        "media": media,
        "length": length,
        "playlist_index": index,   # runtime only
        "notes": notes,
    }


def parse_playlist(entries):
    """Parse a playlist, deduping repeated sequences and flagging problems."""
    rows, seen, issues = [], {}, []
    media_owners = {}
    for i, (seq, media, length) in enumerate(entries, start=1):
        row = parse_entry(seq, media, length, i)
        k = row["key"]
        if k in seen:
            issues.append({"type": "duplicate_entry", "key": k,
                           "detail": f"'{seq}' appears at positions {seen[k]} and {i}"})
            continue
        seen[k] = i
        media_owners.setdefault(media, []).append((k, seq))
        rows.append(row)
    for media, owners in media_owners.items():
        if len({k for k, _ in owners}) > 1:
            issues.append({"type": "shared_media", "key": owners[0][0],
                           "detail": f"{len(owners)} sequences share '{media}': "
                                     + ", ".join(s for _, s in owners)})
    titles = {}
    for r in rows:
        titles.setdefault(r["title"].lower(), []).append(r)
    for t, group in titles.items():
        if len(group) > 1:
            issues.append({"type": "ambiguous_title", "key": group[0]["key"],
                           "detail": f"{len(group)} songs would display as '{group[0]['title']}': "
                                     + ", ".join(g["sequence"] for g in group)})
    for r in rows:
        for n in r["notes"]:
            issues.append({"type": "parse_note", "key": r["key"], "detail": n + f" ({r['media']})"})
    return rows, issues
