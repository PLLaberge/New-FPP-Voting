#!/bin/sh
# Runs after FPP clones this repo into $PLUGINDIR/New-FPP-Voting. Must be
# idempotent — FPP re-runs this on every update, not just the first install.
set -e
. "${FPPDIR}/scripts/common"

REPO_NAME="New-FPP-Voting"
PORT="8000"
PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${MEDIADIR}/plugindata/${REPO_NAME}"
DB_PATH="${DATA_DIR}/fppvote.db"

cd "$PLUGIN_DIR"

# A fresh venv every install/update, never the system python3 — FPP's python
# is a PEP-668 externally-managed environment, and the FPP-recommended
# alternative (declaring `dependencies.python` in pluginInfo.json) installs
# system-wide with `pip install --break-system-packages`, which risks a
# version clash with FPP's own packages or another plugin's. Deliberately not
# doing that here. Recreating the venv on every re-run is cheap at this
# project's size and avoids any stale-wheel surprises after an update.
rm -rf venv
python3 -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt

# The database lives outside the git-managed plugin directory, in FPP's own
# plugindata area, so an uninstall/reinstall (which replaces $PLUGIN_DIR)
# cannot take vote history or curated categories with it. tools/init_db.py is
# additive and safe to run on every install — see its own docstring.
mkdir -p "$DATA_DIR"
chown -R "${FPPUSER}:${FPPGROUP}" "$DATA_DIR"
venv/bin/python tools/init_db.py --db "$DB_PATH"

# Render the systemd unit. sed rather than envsubst — envsubst is not
# guaranteed present on every FPP image, sed always is.
sed \
  -e "s#__PLUGIN_DIR__#${PLUGIN_DIR}#g" \
  -e "s#__DB_PATH__#${DB_PATH}#g" \
  -e "s#__FPP_USER__#${FPPUSER}#g" \
  -e "s#__FPP_GROUP__#${FPPGROUP}#g" \
  -e "s#__PORT__#${PORT}#g" \
  "${PLUGIN_DIR}/deploy/fppvote.service.template" > /etc/systemd/system/fppvote.service

systemctl daemon-reload
systemctl enable --now fppvote

echo "FPP Voting installed. Service: systemctl status fppvote"
echo "Local URL: http://localhost:${PORT}/  (admin: http://localhost:${PORT}/admin)"
echo "Set an admin token (FPPVOTE_ADMIN_TOKEN in the systemd unit) before this"
echo "goes out through the Cloudflare Tunnel — see docs/DEPLOY.md."
