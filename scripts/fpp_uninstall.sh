#!/bin/sh
# Undo every side effect fpp_install.sh made OUTSIDE the plugin directory —
# FPP deletes the directory itself, but nothing else. Idempotent: safe to run
# twice, and safe to run against a partial/failed install.
set -e
. "${FPPDIR}/scripts/common"

systemctl disable --now fppvote 2>/dev/null || true
rm -f /etc/systemd/system/fppvote.service
systemctl daemon-reload

# Deliberately NOT removing $MEDIADIR/plugindata/New-FPP-Voting — that is
# vote history and curated categories, and a reinstall (or an update that
# happens to look like a reinstall) should not lose either. Remove it by hand
# if you actually want a clean slate.
echo "FPP Voting service removed. Vote data was left in place at"
echo "${MEDIADIR}/plugindata/New-FPP-Voting — delete it by hand if you want a clean slate."
