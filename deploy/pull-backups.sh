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
# .state/ is not pulled: it is backup.sh's lock and the record of its last run
# (completion task 3.6), the host's own bookkeeping, not a copy.
#
# The warehouse exports (Task 3.3) are the exception to "keeps everything":
# each is a few hundred MB and the host makes one a night, so the main rsync
# excludes warehouse/ and a second step pulls only the host's NEWEST export and
# each month's newest (the host keeps every month-end). Each is pulled into
# warehouse/DATE.part, checked -- export.json, load.sql, and every Parquet file
# load.sql names -- and only then renamed to DATE; one already here is not pulled
# again. Then, and only when the newest arrived whole, the laptop keeps its 3
# newest exports and each month's newest and deletes the rest: a failed or
# partial pull prunes nothing, so the only off-box copy is never the one
# deleted.
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
ssh_cmd='ssh -o BatchMode=yes -o ConnectTimeout=20'
rsync -a --partial --timeout=120 --exclude='*.part' --exclude='*.part-journal' --exclude=warehouse/ --exclude=.state/ \
	-e "$ssh_cmd" \
	"$host:$src" "$dest/" || rc=$?
if [ "$rc" -ne 0 ]; then
	printf 'pull-backups: rsync exited %s; the copy under %s may be incomplete or unchanged\n' "$rc" "$dest" >&2
	exit 1
fi

# --- the warehouse exports ---------------------------------------------------------
wdest="$dest/warehouse"
# whole_export DIR: export.json, load.sql and every Parquet file load.sql names.
whole_export() {
	local d="$1" f
	[ -f "$d/export.json" ] && [ -f "$d/load.sql" ] || return 1
	for f in $(sed -n "s/.*FROM '\([^']*\)'.*/\1/p" "$d/load.sql" | sed 's|.*/||'); do
		[ -s "$d/$f" ] || return 1
	done
}
# newest_and_month_ends: the dates on stdin (sorted), the newest and each month's newest, sorted.
newest_and_month_ends() {
	awk '{ last[substr($0, 1, 7)] = $0; n = $0 } END { for (m in last) print last[m]; if (n != "") print n }' | sort -u
}
listing=$(rsync --list-only --timeout=120 -e "$ssh_cmd" "$host:${src}warehouse/" 2>/dev/null) || {
	printf 'pull-backups: could not list %s:%swarehouse/; no export pulled, nothing pruned\n' "$host" "$src" >&2
	exit 1
}
remote=$(printf '%s\n' "$listing" | awk '{ print $NF }' | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort || true)
newest=$(printf '%s\n' "$remote" | grep . | tail -n 1 || true)
if [ -n "$newest" ]; then
	mkdir -p "$wdest"
	for d in $(printf '%s\n' "$remote" | grep . | newest_and_month_ends); do
		if whole_export "$wdest/$d"; then
			continue
		fi
		rm -rf "${wdest:?}/$d.part"
		erc=0
		rsync -a --partial --timeout=120 -e "$ssh_cmd" "$host:${src}warehouse/$d/" "$wdest/$d.part/" || erc=$?
		if [ "$erc" -ne 0 ] || ! whole_export "$wdest/$d.part"; then
			rm -rf "${wdest:?}/$d.part"
			printf 'pull-backups: the export %s did not arrive whole (rsync exit %s); nothing pruned\n' "$d" "$erc" >&2
			exit 1
		fi
		rm -rf "${wdest:?}/$d"
		mv -- "$wdest/$d.part" "$wdest/$d"
		printf 'pull-backups: export %s pulled and checked\n' "$d"
	done
	# Only now, with the newest here and whole: the 3 newest and each month's newest stay.
	local_dates=$(find "$wdest" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||' | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort || true)
	keep=" $( (printf '%s\n' "$local_dates" | grep . | tail -n 3; printf '%s\n' "$local_dates" | grep . | newest_and_month_ends) | sort -u | tr '\n' ' ') "
	for d in $local_dates; do
		case "$keep" in *" $d "*) continue ;; esac
		rm -rf "${wdest:?}/$d"
		printf 'pull-backups: pruned the laptop export %s (3 newest and each month'"'"'s newest kept)\n' "$d"
	done
fi

count=$(find "$dest" -maxdepth 1 -type f | grep -c . || true)
newest=$(find "$dest" -maxdepth 1 -type f -name 'desk-*.db' | sed 's|.*/||' | sort | tail -n 1)
printf 'pull-backups: ok -- %s files under %s, newest journal copy %s\n' "$count" "$dest" "${newest:-none yet}"
