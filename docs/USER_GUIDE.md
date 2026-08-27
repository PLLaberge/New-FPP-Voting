# FPP Voting — User Guide

Your own reference for installing/updating the plugin and running the
`/admin` page day to day. Written so you can pick this back up after months
away without having to re-learn it. For the technical "why" behind any of
this, see `CLAUDE.md`; for the one-time Cloudflare Tunnel setup, see
`DEPLOY.md`.

---

## Quick start

To resume working on this project with Claude Code, from a terminal on your
laptop:

```bash
cd ~/projects/"FPP Voting"
source .venv/bin/activate
claude
```

That puts you back in the project directory with the Python virtualenv
active and starts a Claude Code session there — the rest of this guide is
for the *deployed* plugin on the Pi, not this step.

---

## 1. Installing / updating the plugin

### If it's already installed (the normal case)

This is what you'll use almost every time — a routine code update, no
uninstall involved:

```bash
cd ~/media/plugins/New-FPP-Voting
git pull
sudo systemctl restart fppvote
```

Then confirm it came back up clean:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Look for `"ok": true`. If you changed something schema-related recently
(you'll know because I'll have told you), it's worth downloading a backup
from `/admin` → **Backup** first, just in case — see §2.4.

**If `git pull` fails with `Permission denied` on something under `.git/`** —
this happens if FPP's own Plugin Manager touched the repo as root at some
point:

```bash
cd ~/media/plugins/New-FPP-Voting
sudo chown -R $(whoami):$(whoami) .
git pull
sudo systemctl restart fppvote
```

### First-time install, via FPP's Plugin Manager (Install from URL)

Only needed if the plugin isn't installed at all yet (a brand new Pi, or
after a deliberate uninstall).

1. **Check your UI Level first.** In FPP's Settings, find the UI Level
   control and make sure it's high enough to show FPP's "Developer" tab
   (level 3+). If the search box in Plugin Manager just says "Find a Plugin"
   (not mentioning `pluginInfo.json`), the level is too low and step 3 below
   won't work no matter what you paste.
2. FPP web UI → **Content Setup → Plugin Manager**.
3. In the search/URL box, paste the **exact pluginInfo.json URL**, not the
   repo URL:
   ```
   https://raw.githubusercontent.com/PLLaberge/New-FPP-Voting/main/pluginInfo.json
   ```
   (A `github.com/.../blob/main/pluginInfo.json` link also works — FPP
   rewrites it automatically. The plain repo URL, `.../New-FPP-Voting.git`,
   will *not* work — the box only recognizes something ending literally in
   `pluginInfo.json`.)
4. Install. FPP clones into `/home/fpp/media/plugins/New-FPP-Voting/` and
   runs the install script, which creates its own Python environment, seeds
   the database if one doesn't already exist, and starts the `fppvote`
   systemd service on port 8000.

### First-time install, by hand over SSH/PuTTY

Use this if Plugin Manager's URL box isn't cooperating, or you just prefer
the terminal:

```bash
cd /home/fpp/media/plugins
git clone https://github.com/PLLaberge/New-FPP-Voting.git
sudo -E bash New-FPP-Voting/scripts/fpp_install.sh
```

The `-E` matters — plain `sudo` resets your environment, and the install
script needs `$FPPDIR` (normally set for your login shell) to find FPP's own
helper scripts. Without `-E` you'll get an error like `.../scripts/common:
No such file or directory`.

### After any *reinstall* (not a routine update) — check the admin token

Uninstalling and reinstalling regenerates the systemd service file from
scratch, which **wipes the admin token** if you'd set one (it lives in a
separate systemd drop-in file, `fppvote.service.d/override.conf`, that a
plain `git pull` never touches — but a fresh install does).

After any reinstall, open `https://songvote.ca/admin` and confirm it prompts
for a token before showing anything. If it loads straight in with no
prompt, the site is open to the public — re-add the token:

```bash
sudo mkdir -p /etc/systemd/system/fppvote.service.d
```
```bash
printf '[Service]\nEnvironment=FPPVOTE_ADMIN_TOKEN=YOUR_TOKEN_HERE\n' | sudo tee /etc/systemd/system/fppvote.service.d/override.conf
```
```bash
sudo systemctl daemon-reload
```
```bash
sudo systemctl restart fppvote
```

Run those as four separate commands, not pasted as one block — a multi-line
paste has silently failed in PuTTY before.

---

## 2. The `/admin` page

Open it at `https://songvote.ca/admin` (or `http://<pi-ip>:8000/admin` on
your home network). The top of the page has an **Open voter page ↗** link
and your admin token status.

The page has two layers: a handful of cards that apply to the **whole
install**, then tabs for each **show** (Christmas, New Year's Eve,
Halloween) with settings specific to that show's curation.

### 2.1 Setting the admin token (do this before going public)

Click **Set admin token** in the top-right, top bar. Type a token and
confirm. It's stored in this browser only (localStorage) — if you use a
different browser or device, you'll be prompted again the first time.
Leaving it blank/never setting one means `/admin` is wide open, which is
fine on your home network but must be set before the site is reachable
through the Cloudflare Tunnel.

### 2.2 Voting — start, stop, and the global rules

The **Voting** card at the top:

- **Start Voting / Stop Voting** button — pausing doesn't touch FPP or the
  playlist at all; it just hides the song list on the voter page behind a
  "not right now" message and refuses new votes. Safe to flip mid-show.
- Live **viewer count** (green dot = at least one phone currently connected).
- **Votes per round (1–3)** and **Cooldown songs** — one setting for the
  whole install, not per show. Change either and click **Save voting rules**.

### 2.3 Vote Tally

A running count per song:
- **Cumulative** — since the last reset (or all-time if you've never reset).
- **Today** — resets automatically at local midnight, independent of the
  cumulative reset.
- **Export CSV** — downloads both columns for record-keeping.
- **Reset cumulative total** — moves the cumulative starting point to right
  now. Nothing is deleted; every vote ever cast stays in the database. Safe
  to undo by hand later if you ever needed to (ask me).

This tally is global across every show, on purpose — it's not split by
Christmas vs. New Year's.

### 2.4 Recent Activity

The last 50 votes cast, newest first — the fastest way to eyeball "is this
actually working right now" during a show, without waiting on the full
tally.

### 2.5 Backup

**Download backup** grabs the whole database file — every vote ever cast
and every curated category, in one file. Worth doing:
- Before a full plugin **reinstall** (an update via `git pull` doesn't touch
  the database at all, so this is only needed before something more
  invasive).
- Before you do a big batch of category/song cleanup you might regret.

### 2.6 Categories

One shared set of category chips for the **whole install** — not one list
per show. A song carries the same tags no matter which playlist it's
currently in.

- **Reorder**: the ↑ / ↓ arrows on each row — this is chip order on the
  voter page.
- **Rename**: click into the text field and edit directly.
- **Remove**: the ✕ button. If any songs still carry that category, you'll
  get a warning after saving — the assignment isn't deleted, the chip just
  stops rendering until you either bring the category back or manually
  re-tag those songs.
- **Add**: type a name in the box at the bottom and click **Add**, then
  **Save category list** to commit the whole list (reorders, renames, adds,
  and removals all save together).

The number next to each category is how many currently-active songs carry
it.

### 2.7 Show tabs

Below the global cards, one tab per show (**Christmas 2025**, **New Year's
Eve 2026**, **Halloween 2026**). A tab shows an "inactive" badge if the show
itself is turned off, and a "N to review" badge if songs are still missing
categories. Click a tab to see that show's settings and song list below.

**What's per-show vs. global, so you don't go looking in the wrong place:**
category *vocabulary* is global (§2.6); which songs are *voteable right
now* follows whatever FPP is actually playing, not a chosen show; but each
show still has its own header text, tagline, theme, and its own "Reconcile"
target playlist.

### 2.8 Voter Page Header Text

The two announcement lines shown on the voter page, right under the icons
and above "Vote for the next song." Neither has to be a show name or a date
— put whatever you want here (a shout-out, a note about the night, anything).
**Line 1** is larger, **Line 2** sits smaller underneath it. Edit and click
**Save header text**.

### 2.9 Settings (per show)

- **FPP playlist name** — the exact playlist name this show's "Reconcile"
  button pulls from FPP. Only relevant to §2.10 below — it does **not**
  control which songs are voteable; that already follows whatever's actually
  playing, regardless of show.
- **Tagline** — small text on the voter page's now-playing card.
- **Theme** — `christmas`, `nye`, or `halloween`. Controls the accent color
  and the background animation (snow / fireworks / ghosts) on the voter page
  when that show is the one showing.
- **Active** checkbox — turning a show off hides its tab-specific extras but
  doesn't touch voting itself.

Click **Save settings** to commit.

### 2.10 Reconcile with FPP

Pulls the live playlist (the one named in this show's Settings) straight
from FPP and syncs songs in or out of this show's curated list:
- New songs in the playlist get added.
- Songs no longer in the playlist are marked inactive (never deleted — their
  categories and vote history are kept, and they come right back if the
  song returns to the playlist later).
- **Never touches a category you've already set by hand.** This is safe to
  run as often as you like — re-running it after nothing changed does
  nothing.

Click **Reconcile now**. The result panel shows counts (added/reactivated/
deactivated/unchanged/still need review) plus any parser notes.

You generally only need this after editing a playlist on the Pi itself —
adding or removing sequences from the actual FPP playlist. It's *not* how
you stop a song from being voted on — for that, see §2.11's Exclude checkbox.

### 2.11 Songs

The full song list for whichever show tab you're on, with a search box
(searches title and artist).

- **Title / Artist / Year** — click directly on the text to edit it inline.
  Press **Enter** or click away to save, **Escape** to cancel. If you set a
  custom title, a small "parsed as: ..." note shows what the original
  filename-derived title was; typing the override back to match that
  original clears the override.
- **Categories** — click any chip to toggle it on/off for that song. Chips
  come from the global vocabulary (§2.6).
- **Status** — badges: "inactive" (not currently in this show's playlist,
  per the last Reconcile) and "needs review" (no categories assigned yet —
  it's still fully voteable, just uncategorized).
- **Voting** column:
  - **Exclude from voting** checkbox — stops this specific song from being
    voted on, *immediately*, even if it's still sitting right there in the
    live FPP playlist. This is the manual override the "Status" badges
    above don't give you — "inactive" is just Reconcile's bookkeeping and by
    itself does **not** stop a song from being voted on.
  - **Delete** button — only appears once a song is excluded. Permanently
    removes it, but only if it's never been played or voted on (the button
    will tell you if it refuses, and why). This can't be undone, so it's
    meant for cleaning up junk/duplicate entries, not retiring songs with
    real history.

---

## 3. Quick troubleshooting reference

| Symptom | Likely cause / fix |
|---|---|
| `/admin` loads with no token prompt after a reinstall | The systemd admin-token drop-in got wiped — see §1, "After any reinstall." |
| `git pull` says `Permission denied` | A prior root-run git operation left files root-owned — see §1's `chown` fix. |
| Plugin Manager's URL box does nothing | You need the exact `pluginInfo.json` URL (not the repo URL) and UI Level 3+ — see §1. |
| A song shows "inactive" but people can still vote for it | Expected — "inactive" doesn't gate voting. Use the Exclude checkbox (§2.11) instead. |
| Fireworks/ghosts aren't showing for New Year's/Halloween | The *theme* only switches once that show's songs are the majority of what's actually playing — a couple of cross-listed songs isn't enough. Not a bug if it's mostly a different show's playlist. |
| Something looks broken after an update | `curl -s http://localhost:8000/api/health` — `"ok": true` means the service and FPP connection are both fine; the `"fpp"` block explains what's wrong if not. |

When in doubt, or before anything that feels risky, download a backup
(§2.5) first — it costs nothing and every vote is safe either way, since
votes are never deleted by anything in this app.
