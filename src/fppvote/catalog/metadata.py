"""Curated metadata, keyed by song_key (derived from sequenceName).
This is the `songs` table: artist and year are properties of the song and are
true regardless of which show plays it. year=None means "needs review"."""

# Show definitions — display copy only. Lives here rather than in a script
# because both build_catalog.py and init_db.py need it and two copies would
# drift; once a database exists the `shows` table is the source of truth and
# the admin page edits it, not this file.
#
# playlist_name is what the adapter asks FPP for. FPP stores playlists as JSON
# files in ~/media/playlists/ and refers to them by filename WITHOUT the .json,
# so 'NY_Dance_Party.json' on disk is 'NY_Dance_Party' here.
#
# Christmas and New Year's are the real names, from Paulin (2026-08-16).
# Halloween is still a placeholder — that playlist does not exist yet.
#
# Matching is tolerant of a .json suffix and of case, so either form works;
# see Follower.resolve_show. Confirm against a capture when convenient:
# tests/fixtures/captured/playlists_*.json is FPP's own list.
#
# No "categories" key any more (2026-08-26) — categories are global now, not
# per show. See CATEGORIES below.
SHOW_DEFS = {
    "christmas": {
        "name": "Christmas 2025",
        "playlist_name": "All_Xmas_Songs - Alphabetic",
        "tagline": "The winner plays next.",
        "note": "",
        "theme": "christmas",
    },
    "nye": {
        "name": "New Year's Eve 2026",
        "playlist_name": "NY_Dance_Party",
        "tagline": "The winner plays next.",
        "note": "Dec 29 – Jan 3",
        "theme": "nye",
    },
    "halloween": {
        "name": "Halloween 2026",
        "playlist_name": "Halloween 2026",   # placeholder: no such playlist yet
        "tagline": "Catalog not built yet.",
        "note": "New show — playlist still to come",
        "theme": "halloween",
    },
}

# The controlled category vocabulary, in the order the chips appear. Global
# across every show since 2026-08-26 (Paulin: "one set of categories, which
# get applied regardless of the theme or playlist chosen" — a per-show
# vocabulary was "a legacy of the per playlist approach that was originally
# taken"). Previously two lists (10 Christmas, 8 New Year's); this is their
# union, in Christmas's original order with New Year's new-only names
# appended, "Rock" merged into "Rock & Roll" per Paulin's call the same day.
CATEGORIES = [
    "New this year", "Traditional", "Contemporary", "Spiritual", "Crooners",
    "Rock & Roll", "Sing-Along", "Kids & Movies", "Instrumental",
    "Not-So-Christmasy", "Countdown", "Dance Tunes", "Pop", "Throwback",
]

META = {
 # key: (artist, year)
 "christmas-vacation":                              (None, None),
 "christmas-sarajevo-12-24-instrumental":           ("Trans-Siberian Orchestra", 1996),
 "let-it-snow-baby-its-cold-outside":               ("Alex G and Robby Word", None),
 "under-the-sea-the-little-mermaid":                ("Samuel E. Wright", 1989),
 "we-three-kings":                                  ("Alexander Jean", None),
 "frosty-the-snowman":                              ("Ben Rector", 2022),
 "frozen-let-it-go-sing-along-official-disney-hd":  ("Idina Menzel", 2013),
 "carol-of-the-bells":                              ("Monique Danielle", None),
 "feliz-navidad":                                   ("José Feliciano", 1970),
 "jingle-bell-rock":                                ("Bobby Helms", 1957),
 "silent-night-feat-reba-mcentire":                 ("Kelly Clarkson", 2021),
 "golden-kpop-demon-hunters":                       ("HUNTR/X", 2025),
 "do-you-hear-what-i-hear":                         ("Spiraling", None),
 "linus-and-lucy":                                  ("Vince Guaraldi Trio", 1964),
 "the-christmas-song-merry-christmas":              ('Nat "King" Cole', 1946),
 "frozen-2-into-the-unknown":                       ("Idina Menzel", 2019),
 "zero":                                            ("Imagine Dragons", 2018),
 "jingle-bells-epic-version":                       (None, None),
 "santa-wont-you-bring-me-love":                    (None, None),
 "first-snow-instrumental":                         ("Trans-Siberian Orchestra", 1996),
 "music-box-dancer-radio-version":                  ("DJ Schwede", None),
 "how-far-ill-go":                                  ("Auli'i Cravalho", 2016),
 "takin-care-of-christmas":                         ("Randy Bachman", None),
 "disney-medley-happily-ever-after":                ("Disney Medley", None),
 "believer":                                        ("Imagine Dragons", 2017),
 "anti-hero":                                       ("Taylor Swift", 2022),
 "mele-kalikimaka":                                 ("Bing Crosby", 1950),
 "300-violin-orchestra":                            (None, None),
 "little-drummer-boy-live":                         ("for KING & COUNTRY", None),
 "the-12-days-of-christmas":                        (None, None),
 "o-tannenbaum":                                    ("Mannheim Steamroller", 1984),
 "christmas-every-day":                             ("Simple Plan", None),
 "snoopys-christmas":                               ("The Royal Guardsmen", 1967),
 "oh-christmas-tree":                               ("Kids Now", None),
 "danger-zone-top-gun":                             ("Kenny Loggins", 1986),
 "what-a-wonderful-world-single":                   ("Louis Armstrong", 1967),
 "dance-the-night":                                 ("Dua Lipa", 2023),
 "sounding-joy":                                    ("Ellie Holcomb", None),
 "star-wars-funk-final":                            ("DJ Disco", None),
 "the-spirit-of-christmas-medley":                  ("Michael W. Smith & Friends", None),
 "my-favorite-things":                              ("Pentatonix", 2014),
 "carol-of-the-bells-foster-instrumental":          ("David Foster", 1993),
 "barbie-girl":                                     ("Aqua", 1997),
 "light-the-lights-edited":                         ("Lynne and Reg Dickson", None),
 "wizards-in-winter-instrumental":                  ("Trans-Siberian Orchestra", 2004),
 "its-the-most-wonderful-time-of-the-year":         ("Andy Williams", 1963),
 "taylor-swift-show":                               ("Taylor Swift", None),
 "hallelujah":                                      ("Pentatonix", 2016),
 "a-star-to-follow-short-short-xmas":               ("Trans-Siberian Orchestra", 2004),
 "here-comes-the-sun":                              ("The Beatles", 1969),
 "star-wars-imperial-march-x-carol-of-the-bells":   ("Samuel Kim Music", None),
 "queen-of-the-winter-night":                       ("Trans-Siberian Orchestra", 2004),
 "huron-carol-short-version":                       ("Heather Dale", None),
 "we-dont-talk-about-bruno":                        ("Lin-Manuel Miranda", 2021),
 "i-want-a-hippopotamus-for-christmas":             ("Gayla Peevey", 1953),
 "i-gotta-feeling":                                 ("Black Eyed Peas", 2009),
 "white-christmas-feat-ken-darby":                  ("Bing Crosby", 1942),
 "youre-a-mean-one-mr-grinch":                      ("Pentatonix", 2016),
 "dance-of-the-sugar-plum-fairy":                   ("Michael Maxwell", None),
 "cant-stop-the-feeling-film-final":                ("Justin Timberlake", 2016),
 "i-heard-the-bells-on-christmas-day":              ("Casting Crowns", 2008),
 "light-of-christmas-feat-tobymac":                 ("Owl City", 2012),
 "life-is-a-highway":                               ("Rascal Flatts", 2006),
 "trim-up-the-tree":                                ("MGM Studio Chorus & Orchestra", 1966),
 "nutcracker-trepak-russian-dance":                 ("Tchaikovsky", 1892),
 # NYE-only
 "darude-sandstorm":                                ("Darude", 1999),
 "the-movie-medley":                                ("Straight No Chaser", None),
 "toccata-and-fugue-paranormal-remix":              ("J.S. Bach, remixed", None),
 "dont-start-now":                                  ("Dua Lipa", 2019),
 "shut-up-and-dance":                               ("Walk the Moon", 2014),
 "auld-lang-syne":                                  (None, None),
}

# One set of categories per song, global (2026-08-26) — the union of the old
# per-show CHRISTMAS_CATS/NYE_CATS, "Rock" merged into "Rock & Roll". A song
# curated differently under the two former shows now carries every tag it
# ever had (e.g. "zero" was Rock & Roll/Not-So-Christmasy at Christmas and
# Rock/Pop at New Year's — it is now Rock & Roll, Not-So-Christmasy and Pop,
# full stop). Paulin's call, and his to correct by hand from here — this was
# a one-time computed merge, not a judgement call software should keep making.
SONG_CATEGORIES = {
 "300-violin-orchestra":                         ['New this year', 'Contemporary', 'Instrumental'],
 "a-star-to-follow-short-short-xmas":            ['Traditional', 'Rock & Roll'],
 "anti-hero":                                    ['Not-So-Christmasy', 'Dance Tunes', 'Pop'],
 "auld-lang-syne":                               ['Countdown'],
 "barbie-girl":                                  ['Kids & Movies', 'Not-So-Christmasy', 'Dance Tunes', 'Pop', 'Throwback'],
 "believer":                                     ['Rock & Roll', 'Not-So-Christmasy', 'Pop'],
 "cant-stop-the-feeling-film-final":             ['Kids & Movies', 'Not-So-Christmasy', 'Countdown', 'Dance Tunes', 'Pop'],
 "carol-of-the-bells":                           ['Traditional'],
 "carol-of-the-bells-foster-instrumental":       ['Traditional', 'Contemporary', 'Instrumental'],
 "christmas-every-day":                          ['Contemporary', 'Rock & Roll'],
 "christmas-sarajevo-12-24-instrumental":        ['Traditional', 'Rock & Roll', 'Instrumental'],
 "christmas-vacation":                           ['Sing-Along', 'Not-So-Christmasy'],
 "dance-of-the-sugar-plum-fairy":                ['Traditional', 'Contemporary'],
 "dance-the-night":                              ['Not-So-Christmasy'],
 "danger-zone-top-gun":                          ['Rock & Roll', 'Not-So-Christmasy', 'Throwback'],
 "darude-sandstorm":                             ['Instrumental', 'Dance Tunes'],
 "disney-medley-happily-ever-after":             ['Kids & Movies'],
 "do-you-hear-what-i-hear":                      ['Spiritual', 'Rock & Roll'],
 "dont-start-now":                               ['New this year', 'Dance Tunes', 'Pop'],
 "feliz-navidad":                                ['Contemporary', 'Sing-Along'],
 "first-snow-instrumental":                      ['Contemporary', 'Rock & Roll', 'Instrumental'],
 "frosty-the-snowman":                           ['Contemporary', 'Crooners', 'Kids & Movies'],
 "frozen-2-into-the-unknown":                    ['Kids & Movies', 'Not-So-Christmasy'],
 "frozen-let-it-go-sing-along-official-disney-hd":['Sing-Along', 'Kids & Movies', 'Not-So-Christmasy'],
 "golden-kpop-demon-hunters":                    ['New this year', 'Kids & Movies', 'Not-So-Christmasy', 'Pop'],
 "hallelujah":                                   ['Contemporary', 'Spiritual'],
 "here-comes-the-sun":                           ['Rock & Roll', 'Not-So-Christmasy', 'Throwback'],
 "how-far-ill-go":                               ['Kids & Movies', 'Not-So-Christmasy'],
 "huron-carol-short-version":                    ['Traditional', 'Spiritual'],
 "i-gotta-feeling":                              ['Not-So-Christmasy', 'Countdown', 'Dance Tunes', 'Pop'],
 "i-heard-the-bells-on-christmas-day":           ['Contemporary', 'Spiritual'],
 "i-want-a-hippopotamus-for-christmas":          ['Sing-Along', 'Kids & Movies'],
 "its-the-most-wonderful-time-of-the-year":      ['Crooners', 'Sing-Along'],
 "jingle-bell-rock":                             ['Rock & Roll', 'Sing-Along'],
 "jingle-bells-epic-version":                    ['Traditional', 'Rock & Roll'],
 "let-it-snow-baby-its-cold-outside":            ['Contemporary'],
 "life-is-a-highway":                            ['Rock & Roll', 'Kids & Movies', 'Not-So-Christmasy'],
 "light-of-christmas-feat-tobymac":              ['Contemporary', 'Spiritual'],
 "light-the-lights-edited":                      ['Contemporary'],
 "linus-and-lucy":                               ['Kids & Movies'],
 "little-drummer-boy-live":                      ['Spiritual', 'Rock & Roll'],
 "mele-kalikimaka":                              ['Crooners'],
 "music-box-dancer-radio-version":               ['Contemporary', 'Instrumental', 'Dance Tunes'],
 "my-favorite-things":                           ['Contemporary', 'Kids & Movies'],
 "nutcracker-trepak-russian-dance":              ['Traditional'],
 "o-tannenbaum":                                 ['Traditional', 'Contemporary'],
 "oh-christmas-tree":                            ['Traditional', 'Sing-Along', 'Kids & Movies'],
 "queen-of-the-winter-night":                    ['Rock & Roll'],
 "santa-wont-you-bring-me-love":                 ['New this year', 'Contemporary'],
 "shut-up-and-dance":                            ['Rock & Roll', 'Dance Tunes', 'Pop'],
 "silent-night-feat-reba-mcentire":              ['Contemporary', 'Spiritual'],
 "snoopys-christmas":                            ['New this year', 'Rock & Roll', 'Sing-Along'],
 "sounding-joy":                                 ['Contemporary', 'Spiritual'],
 "star-wars-funk-final":                         ['Kids & Movies', 'Not-So-Christmasy', 'Dance Tunes'],
 "star-wars-imperial-march-x-carol-of-the-bells":['Traditional', 'Contemporary', 'Kids & Movies'],
 "takin-care-of-christmas":                      ['Rock & Roll'],
 "taylor-swift-show":                            ['Not-So-Christmasy', 'Pop'],
 "the-12-days-of-christmas":                     ['Contemporary', 'Sing-Along'],
 "the-christmas-song-merry-christmas":           ['Traditional', 'Crooners'],
 "the-movie-medley":                             ['Kids & Movies', 'Pop'],
 "the-spirit-of-christmas-medley":               ['Contemporary', 'Spiritual'],
 "toccata-and-fugue-paranormal-remix":           ['Instrumental', 'Dance Tunes'],
 "trim-up-the-tree":                             ['Sing-Along', 'Kids & Movies'],
 "under-the-sea-the-little-mermaid":             ['Kids & Movies', 'Not-So-Christmasy'],
 "we-dont-talk-about-bruno":                     ['Kids & Movies', 'Not-So-Christmasy', 'Pop'],
 "we-three-kings":                               ['Traditional', 'Spiritual'],
 "what-a-wonderful-world-single":                ['Crooners', 'Not-So-Christmasy', 'Throwback'],
 "white-christmas-feat-ken-darby":               ['Traditional', 'Crooners'],
 "wizards-in-winter-instrumental":               ['Contemporary', 'Rock & Roll', 'Instrumental'],
 "youre-a-mean-one-mr-grinch":                   ['Sing-Along', 'Kids & Movies'],
 "zero":                                         ['Rock & Roll', 'Not-So-Christmasy', 'Pop'],
}

# What the earlier hand-typed song list contained that the real playlists do not
PREVIOUSLY_LISTED_NOT_IN_PLAYLIST = {
 "christmas": ["Hallelujah Chorus (Ambrosian Singers)",
               "Ice Storm (Lindsey Stirling)",
               "Twelve Days of Christmas (Bob & Doug McKenzie)"],
 "nye":       ["Dance the Night (Dua Lipa)"],
}
