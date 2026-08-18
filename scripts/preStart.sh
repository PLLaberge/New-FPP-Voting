#!/bin/sh
# No-op on purpose. FPP Voting's whole point is to keep taking votes through
# an fppd hiccup — see CLAUDE.md's "the playlist should just keep playing" and
# the adapter's unknown/unreachable handling. Tying its start/stop to fppd's
# own restart cycle (the pattern FPP's plugin guidelines otherwise recommend)
# would undo exactly that: every fppd restart would drop the voting page for
# no reason. systemd (see fppvote.service) runs it independently instead.
exit 0
