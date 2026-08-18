#!/bin/sh
# Self-heal only: if the systemd unit is for some reason not running (a
# reboot where it raced fppd's start, someone stopped it by hand and forgot),
# nudge it back on. This is NOT the same as restarting it on every fppd
# start — see preStart.sh — `systemctl start` on an already-running unit is a
# no-op, so this never interrupts a vote in progress.
systemctl is-active --quiet fppvote || systemctl start fppvote 2>/dev/null || true
exit 0
