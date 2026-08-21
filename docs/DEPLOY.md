# Deploying to the Pi

Everything up to here (stages 1–6) has run on a laptop against
`FakeFppAdapter`. This is the one stage that needs the real Pi — I have no
access to it, so these are steps for you to run, not something I did for you.
Come back here with what you saw at each check; that's usually enough to
unblock the next step without needing the whole session again.

Two independent things happen in this stage:

1. **Install the plugin on the Pi**, so it runs the real FPP Voting service
   against your real FPP.
2. **Put a Cloudflare Tunnel in front of it**, so the QR code you print works
   from anyone's phone, not just on your home WiFi.

Do (1) first and confirm it works **on your home network** before touching
(2) — a tunnel in front of something broken just makes the breakage public.

---

## Part 1 — Install the plugin

### Before you start

- Run `python3 tools/capture_fpp.py --host <pi-address>` from your laptop
  first if you haven't yet — see [[project-awaiting-pi-verification]] in
  CLAUDE.md. It's not a hard blocker for installing the plugin, but
  `start_at_item` over HTTP is the least-verified call in the project, and
  it's better to know before a show is depending on it.
- Confirm the playlist names in `src/fppvote/catalog/metadata.py`
  (`SHOW_DEFS[...]["playlist_name"]`) still match what's actually in
  `~/media/playlists/` on the Pi. They were confirmed once (2026-08-16); if
  you've renamed anything since, fix it there before installing.

### Install

FPP's Plugin Manager installs anything it can `git clone`, whether or not
it's in the official plugin catalog:

1. On the Pi's web UI: **Content Setup → Plugin Manager**.
2. Look for an "Add Plugin from URL" / "Install from URL" field (wording
   varies by FPP version).
3. Paste: `https://github.com/PLLaberge/New-FPP-Voting.git`
4. Install. FPP clones the repo into
   `/home/fpp/media/plugins/New-FPP-Voting/` and runs
   `scripts/fpp_install.sh`, which:
   - creates a Python virtualenv **inside the plugin folder** (never touches
     FPP's system Python — see CLAUDE.md's "Reliability" section for why)
   - seeds `fppvote.db` in `/home/fpp/media/plugindata/New-FPP-Voting/` if it
     doesn't already exist
   - installs and starts a systemd service, `fppvote`, listening on port 8000

If your FPP version has no "install from URL" option, SSH in and do the
equivalent by hand:

```bash
cd /home/fpp/media/plugins
git clone https://github.com/PLLaberge/New-FPP-Voting.git
sudo bash New-FPP-Voting/scripts/fpp_install.sh
```

### Check

```bash
systemctl status fppvote        # active (running)
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

`"ok": true` means it can see FPP and has at least one show configured. If
`"ok": false`, the `"fpp"` block in that same response says why (usually
`"reachable": false`, meaning it can't reach `fppd` — check `FPPVOTE_FPP_URL`
in `/etc/systemd/system/fppvote.service`, which should be
`http://localhost` since the plugin runs on the same Pi).

From a laptop or phone **on the same WiFi**, open `http://<pi-ip>:8000/` —
that's the real voter page against real hardware. Start a playlist on the
Pi and confirm the "now playing" card picks it up.

### Set an admin token before going further

`/admin` is wide open until you set one — anyone who can reach it can change
categories or the vote allowance. Do this now, before Part 2 puts the page on
the public internet:

```bash
sudo systemctl edit fppvote
```

Add:

```ini
[Service]
Environment=FPPVOTE_ADMIN_TOKEN=<pick something long and random>
```

Then `sudo systemctl restart fppvote`. Open `/admin` again — it should now
prompt for the token before anything loads.

### If something's wrong

- **`systemctl status fppvote` shows failed** — `journalctl -u fppvote -n 50`
  for the actual error. The most likely first-run failure is
  `python3-venv` missing; `pluginInfo.json` declares it as a dependency, but
  if your FPP version predates that mechanism, `sudo apt install python3-venv`
  and re-run `scripts/fpp_install.sh` by hand.
- **`"reachable": false` in `/api/health`** — `FPPVOTE_FPP_URL` should be
  `http://localhost`, not the Pi's LAN IP; using the IP has been seen to
  break if the Pi's own hostname resolution is unusual. If MQTT is configured
  in FPP's settings and you want the faster event-driven path instead of 1Hz
  polling, set `FPPVOTE_MQTT_HOST=localhost` the same way you set the admin
  token above.
- **Port 8000 already used by something else** — edit
  `/etc/systemd/system/fppvote.service`, change the `--port` in `ExecStart`,
  `daemon-reload`, restart. `menu.inc`'s links follow `FPPVOTE_PORT` if you
  set that too (defaults to 8000).

---

## Part 2 — Cloudflare Tunnel

This is what makes the printed QR code work for a viewer on their own
cellular data, and what keeps working when you're not home to answer "why
isn't voting working" — see CLAUDE.md architecture decision #1 for why this
is the whole point and not a third-party service in the loop: the tunnel is
outbound-only from the Pi, Cloudflare never sees your vote data unencrypted
in a way that matters here, and there's no port forwarding on your router.

**You need a domain you control**, even a cheap one — Cloudflare's tunnel
needs somewhere to attach a DNS record. If you don't have one yet, this part
waits; Part 1 already gives you a working voting page on home WiFi in the
meantime.

### Setup (run on the Pi)

```bash
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

cloudflared tunnel login          # opens a URL — follow it on any browser, not just the Pi's
cloudflared tunnel create fppvote # writes a credentials file and prints a tunnel ID
```

Point a hostname at it — a subdomain (`vote.yourdomain.com`) or the bare
domain both work identically; replace with whichever you want printed on the
sign:

```bash
cloudflared tunnel route dns fppvote vote.yourdomain.com
```

Create `/etc/cloudflared/config.yml`. The credentials file path is whatever
`tunnel create` printed — it lands under the home directory of whichever user
ran `tunnel login`/`tunnel create` (`/home/fpp/.cloudflared/<id>.json` if you
ran those as `fpp` without `sudo`, which is the normal case; `/root/.cloudflared/`
only if you ran them as root). `sudo cloudflared service install` runs the
service as root regardless, and root can read the file either way:

```yaml
tunnel: fppvote
credentials-file: /home/fpp/.cloudflared/<tunnel-id-from-above>.json

ingress:
  - hostname: vote.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Then run it as its own service, independent of `fppvote` and independent of
`fppd` — same reasoning as `preStart.sh`/`preStop.sh` being no-ops:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### Check

```bash
systemctl status cloudflared
curl -sI https://vote.yourdomain.com/api/health
```

From your phone, **off WiFi** (cellular data, to prove the tunnel and not
just your home network) — `https://vote.yourdomain.com` should load the real
voter page.

### Print the QR code

Once `https://vote.yourdomain.com` works from outside, generate a QR code for
that exact URL (any QR generator; the URL is public and stable, so the
printed sign never goes stale the way an IP-address one would). That's the
sign for the show.

### If something's wrong

- **DNS not resolving yet** — `cloudflared tunnel route dns` can take a few
  minutes to propagate. Retry the phone check after a coffee, not immediately.
- **`http_status:404` for everything** — the `ingress` hostname in
  `config.yml` has to match the DNS record exactly, including subdomain.
- **Works on WiFi, not on cellular** — that means you're actually still
  hitting the LAN IP somehow (browser cache of an old bookmark, e.g.). Clear
  it and retype the tunnel URL.

---

## After this works

Nothing else in the build order needs the Pi. If you find a real bug once
this is live — something FakeFppAdapter never exercised — that's exactly
what `tools/capture_fpp.py` output and a note back are for; the contract
tests exist to catch it precisely once, not need the Pi again for the same
class of problem.
