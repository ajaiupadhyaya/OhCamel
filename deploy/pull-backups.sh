#!/usr/bin/env bash
#
# The off-box copy: pull /var/backups/ohcamel/ from the droplet onto this
# laptop, into ~/Backups/ohcamel/.
#
#   deploy/pull-backups.sh
#
# Runs on the laptop, by hand or from launchd
# (deploy/launchd/com.ohcamel.pull-backups.plist, owner step O15), over the
# `ohcamel` ssh alias the owner already has. This is the free half of the
# finish plan's ruling 11 -- "the off-box copy is the owner's free pull; paid
# backups are a Q10 spending decision" -- and it is deliberately dumb: one
# rsync, archive mode, nothing deleted on the laptop. The host prunes its own
# directory (`ohcamel journal-backup` keeps 14 dailies, 8 Sundays, 5
# pre-deploys; deploy/backup.sh keeps 14 of each copy it makes); the laptop
# keeps everything it has ever pulled, so a copy the host has rotated out is
# still here. In-progress `.part` copies are left behind: a half-written file
# is not a backup.
#
# ssh runs in BatchMode so a key that is not loaded fails at once rather than
# prompting a launchd job that has no terminal; rsync's exit status is the
# run's, and a non-zero one is printed with it so the launchd log says why.
#
#   OHCAMEL_SSH_HOST   the ssh alias (default ohcamel)
#   OHCAMEL_PULL_DIR   the destination (default $HOME/Backups/ohcamel)
#
# deploy/test/pull_backups_test.sh drives this file through a shim `rsync`.

set -euo pipefail

host="${OHCAMEL_SSH_HOST:-ohcamel}"
src=/var/backups/ohcamel/
dest="${OHCAMEL_PULL_DIR:-$HOME/Backups/ohcamel}"

mkdir -p "$dest"
printf 'pull-backups: %s  %s:%s -> %s/\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$host" "$src" "$dest"

rc=0
rsync -a --partial --timeout=120 --exclude='*.part' --exclude='*.part-journal' \
	-e 'ssh -o BatchMode=yes -o ConnectTimeout=20' \
	"$host:$src" "$dest/" || rc=$?
if [ "$rc" -ne 0 ]; then
	printf 'pull-backups: rsync exited %s; the copy under %s may be incomplete or unchanged\n' "$rc" "$dest" >&2
	exit 1
fi

count=$(find "$dest" -maxdepth 1 -type f | grep -c . || true)
newest=$(find "$dest" -maxdepth 1 -type f -name 'desk-*.db' | sed 's|.*/||' | sort | tail -n 1)
printf 'pull-backups: ok -- %s files under %s, newest journal copy %s\n' "$count" "$dest" "${newest:-none yet}"
