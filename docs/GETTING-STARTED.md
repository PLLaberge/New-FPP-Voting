# Getting started

Follow these in order. Each step has a check — if the check fails, stop there
rather than pushing on.

Total time: about 30 minutes, most of it waiting for downloads.

---

## Step 1 — Install WSL  (Windows only, ~10 min)

WSL runs a real Linux system inside Windows. We want it because your Pi runs
Linux, so your laptop then behaves the same way your Pi will.

1. Click Start, type **PowerShell**, right-click it, choose
   **Run as administrator**.
2. Run:

   ```powershell
   wsl --install
   ```

3. **Reboot when it asks.**
4. After reboot an Ubuntu window opens by itself and asks you to create a
   username and password.
   - This is a **Linux** account, unrelated to your Windows login.
   - Use something short and lowercase, e.g. `paulin`.
   - **The password will not appear as you type — not even dots.** That is
     normal Linux behaviour, not a broken keyboard. Type it and press Enter.
   - Remember this password. You need it for `sudo`.

**Check:** you have a prompt that looks like `paulin@DESKTOP-xyz:~$`

From now on, *every command in this guide is typed in that Ubuntu window*, not
in PowerShell.

> **Reopening it later:** Start menu → Ubuntu. Or type `wsl` in any terminal.

---

## Step 2 — Update Ubuntu and install the tools  (~5 min)

Your Windows copies of Python, git and Claude Code do **not** exist inside WSL.
WSL is its own world. Install them again in here — this is expected.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl
```

It will ask for the password you just created.

**Check:**

```bash
python3 --version     # want 3.11 or higher
git --version
```

---

## Step 3 — Tell git who you are

```bash
git config --global user.name "Paulin Laberge"
git config --global user.email "paulinlaberge@gmail.com"
```

**Check:** `git config --global --list` shows both.

---

## Step 4 — Install Claude Code inside WSL

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

If that command fails or has changed, check the current instructions at
<https://docs.claude.com/en/docs/claude-code/setup> — installers move around
and it is not worth fighting a stale command.

Then close and reopen Ubuntu so your PATH picks it up.

**Check:**

```bash
claude --version
```

---

## Step 5 — Put the project somewhere sensible

**This step matters more than it looks.** Keep the project on the *Linux*
filesystem (`~/projects`), **not** under `/mnt/c/...`. Files on the Windows
drive are many times slower from WSL and have permission quirks. This is the
single most common WSL mistake.

```bash
mkdir -p ~/projects
cd ~/projects
```

Now copy the starter repo in. From your Ubuntu window:

```bash
explorer.exe .
```

That opens a Windows Explorer window pointing at your Linux folder. Drag the
unzipped `fpp-voting` folder into it, then back in Ubuntu:

```bash
cd ~/projects/fpp-voting
ls
```

**Check:** you see `README.md`, `CLAUDE.md`, `src`, `tests`.

---

## Step 6 — Set up Python and prove it works

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

**Check:** `29 passed`.

That is the whole toolchain verified — Python, dependencies, and the code we
already wrote. If this passes, nothing later is an environment problem.

> `source .venv/bin/activate` is needed **each time you open a new terminal**.
> Your prompt shows `(.venv)` when it is active.

---

## Step 7 — Start version control

```bash
git init
git add .
git commit -m "Starter: catalog parser, reconciler, schema, adapter interface, voter prototype"
```

**Check:** `git log --oneline` shows one commit.

This is your safety net. From here, anything you break can be undone.

---

## Step 8 — Open Claude Code

```bash
claude
```

It starts in the project folder and reads `CLAUDE.md` automatically, so it
begins with every decision we made — no need to re-explain the project.

Good opening message:

> Read CLAUDE.md and README.md. We're at stage 2 of the build order: the
> database layer. Before writing code, walk me through what you're planning
> and why.

---

## Step 9 — See the voting page in a browser

The tricky bit in WSL: the server runs inside **Linux**, but your browser is on
**Windows**. They can talk to each other, but nothing opens automatically.

### 1. Build the database (once)

```bash
cd ~/projects/fpp-voting
source .venv/bin/activate
python3 scripts/init_db.py
```

**Check:** it prints three shows and finishes without an error.

### 2. Start the server

```bash
FPPVOTE_FAKE=1 uvicorn fppvote.service:app --host 0.0.0.0 --port 8000
```

`FPPVOTE_FAKE=1` runs a pretend show, so you need no Raspberry Pi.

**Check:** the last line says

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**The terminal now looks stuck. It is not.** That is the server running, and it
will sit there until you stop it. Leave this window alone.

### 3. Open it

Easiest way — ignore Linux entirely: open **Edge or Chrome on Windows** as you
normally would, click the address bar, type `localhost:8000` and press Enter.

Or, from Linux, open a **second** Ubuntu window (Start menu → Ubuntu) and run:

```bash
explorer.exe "http://localhost:8000"
```

That hands the address to Windows, which opens your normal browser.

**Check:** you see "Vote for the next song", a song playing at the top, and 65
Christmas songs you can tap.

> `xdg-open` does **not** work here — that is for a Linux desktop, and WSL has
> no desktop. Use `explorer.exe`.

### 4. Try it as two different viewers

Two tabs in the same browser are the **same person** as far as voting is
concerned — they share the voting ID stored in that browser. To be two people:

- one **normal** window, and
- one **private/incognito** window (Ctrl+Shift+N in Chrome, Ctrl+Shift+P in Edge)

Vote in one and watch the other update by itself. That is the WebSocket working.

### 5. Stop the server

Click the terminal running it and press **Ctrl+C**.

---

## If something goes wrong

- **`sudo: command not found` / weird prompt** — you're probably in PowerShell,
  not Ubuntu. Start menu → Ubuntu.
- **`pytest: command not found`** — the venv isn't active. Run
  `source .venv/bin/activate`.
- **`externally-managed-environment` error from pip** — you're outside the
  venv. Same fix. Never use `sudo pip`.
- **Everything is very slow** — the project is probably under `/mnt/c/`.
  Move it to `~/projects`.
- **`localhost:8000` says it can't connect** — check the terminal running the
  server is still showing `Uvicorn running on ...`. If you closed it or pressed
  Ctrl+C, start it again. Make sure you used `--host 0.0.0.0`.
- **`xdg-open: command not found`** — expected. WSL has no Linux desktop; use
  `explorer.exe "http://localhost:8000"` instead.
- **`Address already in use`** — a server is still running from last time.
  Stop it with `pkill -f uvicorn`, then start it again.
- **The terminal won't take commands any more** — that is the server running,
  not a hang. Press Ctrl+C to stop it, or open a second Ubuntu window.
- **Both browser tabs show the same votes as "mine"** — same browser, same
  voting ID. Use a private/incognito window for the second viewer.

Ask me. A stuck environment is not a reflection on you; these tools are
genuinely fiddly the first time.
