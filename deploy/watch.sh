#!/usr/bin/env bash
#
# The on-host watch (finish plan Task 18). Run every 5 minutes as the ohcamel
# user by deploy/systemd/ohcamel-watch.timer; `systemctl start
# ohcamel-watch.service` runs it now, and it can be run by hand:
#
#   deploy/watch.sh
#
# What it checks, each under its own alert key:
#
#   state.<svc>    each expected compose service has a running container
#   health.<svc>   a container reporting (unhealthy) for 3 consecutive runs is
#                  restarted ONCE (`docker restart`) and alerted; runs after
#                  that restart nothing more until it has been healthy again.
#                  A missing or exited container is alerted, never restarted
#                  -- compose's restart policy owns that, and a watch that
#                  starts containers fights a deploy that stopped one.
#   docker         `docker ps` itself answers
#   desk           /api/desk answers, read INSIDE the ohcamel-live container
#                  over plain HTTP on 8081 -- the engine's own port, behind
#                  no proxy, so no credential is needed or held here
#   last_error     /api/desk's last_error is set
#   kill_switch    /api/desk's kill_switch is anything but "clear"
#   sync           last_sync is over 180 s old while clock.is_open is true
#   session.<date> no row in recent_sessions for clock.next_close_date 30 min
#                  after that close
#   signals        no signal file for the day after 01:00 UTC, when the book
#                  has strategies (below)
#   backup         the newest desk-*.db in the backup directory is over 26 h
#                  old, or there is none
#   disk           / over 80% used
#   reboot         /var/run/reboot-required is present
#
# THE CLOCK GUARD. The sync and session checks depend on whether the market is
# open, which this script never guesses: it reads /api/desk's clock block
# (Task 13a). When clock.source is "stale" or "unknown", or clock.read_at is
# in this host's future, both checks are SKIPPED for the run with one log
# line, because a watchdog that guesses the market's state is a false-alarm
# generator. The future read_at is the case the desk itself cannot catch
# (plan commit 08972b1, and the comment at desk/desk.ml's clock_json): the
# desk compares read_at with its own wall clock, so after a backward step of
# the host clock a stale reading still says "venue-clock".
#
# THE SESSION RULE learns each close while the clock is trustworthy: every
# trustworthy reading records next_close_date and next_close under
# closes/ in the state directory, and every trustworthy run checks every
# recorded close that is 30 minutes past. That is what lets the rule fire
# after the venue has rolled next_close on to the following session, which
# it does at the close itself. A close is forgotten when its row lands, or
# after 7 days.
#
# THE SIGNAL RULE. The research service writes <slug>-<date>.json for the New
# York trading day <date> at 19:15 New York (00:16 UTC the next day, see
# service.py). So from 01:00 UTC on day T the file owed is the one for T-1,
# i.e. for the UTC date of (now - 25 h), and it is owed only when the desk
# recorded a session row for that date -- a weekend or a holiday owes
# nothing, and the calendar is the desk's to know, not this script's. The
# strategies are the (name ...) entries of book.sexp's signals block; a book
# without one owes no file. The listing is read inside ohcamel-live, which
# mounts the signals volume read-only at /data/signals.
#
# THE BACKUP RULE reads desk-*.db, the journal's nightly copy, and nothing
# else: backup.sh writes the book's copy every night even when the journal's
# failed, and a pre-deploy-*.db is not the nightly, so "the newest file" would
# hide exactly the miss this exists to catch.
#
# ALERTS ARE EDGE-TRIGGERED. The state directory (~/.local/state/ohcamel-watch)
# holds one file per firing key: the first failure prints `ALERT <key>: ...`,
# a repeat prints nothing, and the first good run after prints `RECOVERED
# <key>: ...`. A check that is skipped neither fires nor recovers. Every line
# goes to stdout, which the unit sends to journald (`journalctl -t
# ohcamel-watch`). When /etc/ohcamel/ops.env holds SLACK_WEBHOOK_URL, each
# ALERT and RECOVERED line is also posted there; when it holds
# HEALTHCHECKS_URL, every run ends with a ping: the bare URL when no key is
# firing, URL/fail when one is (so healthchecks.io notices a watch that stopped
# running, too). The file is read for those two keys only, never sourced, and
# neither URL is printed or put on curl's command line (curl reads it from a
# --config file descriptor).
#
# Exit 0 whether or not anything is firing -- the alerts are the channel, and a
# unit that fails every 5 minutes while the disk is full is noise in
# `systemctl --failed`. Exit 1 only when the script cannot run (no jq).
#
# Environment, for tests and odd hosts (every default is the droplet's):
#   OHCAMEL_WATCH_NOW          epoch seconds to treat as now
#   OHCAMEL_WATCH_STATE_DIR    default $HOME/.local/state/ohcamel-watch
#   OHCAMEL_WATCH_OPS_ENV      default /etc/ohcamel/ops.env
#   OHCAMEL_WATCH_BOOK         default <checkout>/book.sexp
#   OHCAMEL_WATCH_REBOOT_FILE  default /var/run/reboot-required
#   OHCAMEL_WATCH_SERVICES     default "caddy ohcamel-quant ohcamel-live ohcamel-research"
#   OHCAMEL_WATCH_DISK_PATH    default /
#   OHCAMEL_BACKUP_DIR         default /var/backups/ohcamel (backup.sh's)
#
# Needs bash, docker, jq (deploy/provision.sh installs it), df, curl, stat.

set -uo pipefail
export TZ=UTC

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

SYNC_MAX_S=180
SESSION_GRACE_S=1800
BACKUP_MAX_S=$((26 * 3600))
DISK_MAX_PCT=80
UNHEALTHY_RUNS=3
SIGNAL_LAG_S=$((25 * 3600))
CLOSE_FORGET_S=$((7 * 86400))
PROJECT=ohcamel

now=${OHCAMEL_WATCH_NOW:-$(date -u +%s)}
state=${OHCAMEL_WATCH_STATE_DIR:-$HOME/.local/state/ohcamel-watch}
ops_env=${OHCAMEL_WATCH_OPS_ENV:-/etc/ohcamel/ops.env}
book=${OHCAMEL_WATCH_BOOK:-$here/../book.sexp}
reboot_file=${OHCAMEL_WATCH_REBOOT_FILE:-/var/run/reboot-required}
services=${OHCAMEL_WATCH_SERVICES:-caddy ohcamel-quant ohcamel-live ohcamel-research}
disk_path=${OHCAMEL_WATCH_DISK_PATH:-/}
backup_dir=${OHCAMEL_BACKUP_DIR:-/var/backups/ohcamel}

command -v jq >/dev/null 2>&1 || {
	echo "ohcamel-watch: jq is required and not installed (deploy/provision.sh installs it)"
	exit 1
}

mkdir -p "$state/alerts" "$state/closes"

say() { printf 'ohcamel-watch: %s\n' "$*"; }

# ops.env holds KEY=value lines; only the two keys this script uses are read,
# and the file is never sourced (it is root's, and a stray line in it must not
# run as this user).
ops() {
	[ -r "$ops_env" ] || return 0
	sed -n "s/^$1=//p" "$ops_env" | tail -n 1 | sed 's/^"\(.*\)"$/\1/; s/^'"'"'\(.*\)'"'"'$/\1/'
}
slack_url=$(ops SLACK_WEBHOOK_URL)
hc_url=$(ops HEALTHCHECKS_URL)

# curl_to URL [curl args...]: the URL through --config on a file descriptor, so
# it never appears on a command line another user's `ps` can read.
curl_to() {
	local url=$1
	shift
	curl -fsS -m 10 "$@" --config <(printf 'url = "%s"\n' "$url") >/dev/null 2>&1
}

notify() {
	local line="$1"
	say "$line"
	if [ -n "$slack_url" ]; then
		curl_to "$slack_url" -H 'Content-Type: application/json' \
			--data-binary "$(jq -cn --arg t "ohcamel-watch: $line" '{text: $t}')" ||
			say "posting to Slack failed (the alert above is still in the journal)"
	fi
}

# fire KEY MESSAGE / clear KEY: the edge. A key's file holds its message.
fire() {
	local f="$state/alerts/$1"
	if [ ! -e "$f" ]; then
		printf '%s\n' "$2" >"$f"
		notify "ALERT $1: $2"
	fi
}
recover() {
	local f="$state/alerts/$1"
	if [ -e "$f" ]; then
		notify "RECOVERED $1: was: $(cat "$f")"
		rm -f "$f"
	fi
}

utc_date() { jq -rn --argjson e "$1" '$e | strftime("%Y-%m-%d")'; }
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }

# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------
live_name=""
if ps_out=$(docker ps -a \
	--filter "label=com.docker.compose.project=$PROJECT" \
	--filter "label=com.docker.compose.oneoff=False" \
	--format '{{.Label "com.docker.compose.service"}}|{{.Names}}|{{.State}}|{{.Status}}' 2>&1); then
	recover docker
	for svc in $services; do
		row=$(printf '%s\n' "$ps_out" | grep -F "$svc|" | grep "^$svc|" | head -n 1)
		if [ -z "$row" ]; then
			fire "state.$svc" "no $svc container in project $PROJECT"
			continue
		fi
		IFS='|' read -r _ name cstate status <<<"$row"
		if [ "$cstate" != running ]; then
			fire "state.$svc" "$name is $cstate ($status)"
			continue
		fi
		recover "state.$svc"
		[ "$svc" = ohcamel-live ] && live_name=$name
		count_f="$state/unhealthy.$svc" restarted_f="$state/restarted.$svc"
		case "$status" in
		*"(unhealthy)"*)
			n=$(($(cat "$count_f" 2>/dev/null || echo 0) + 1))
			echo "$n" >"$count_f"
			if [ "$n" -ge "$UNHEALTHY_RUNS" ] && [ ! -e "$restarted_f" ]; then
				if docker restart "$name" >/dev/null 2>&1; then
					echo "$now" >"$restarted_f"
					fire "health.$svc" "$name unhealthy for $n consecutive runs; restarted once"
				else
					echo "$now failed" >"$restarted_f"
					fire "health.$svc" "$name unhealthy for $n consecutive runs; docker restart FAILED"
				fi
			fi
			;;
		*"(health: starting)"*)
			# Mid-check, typically just after a restart: neither healthy nor not.
			;;
		*)
			rm -f "$count_f" "$restarted_f"
			recover "health.$svc"
			;;
		esac
	done
else
	fire docker "docker ps failed: $(printf '%s' "$ps_out" | head -n 1)"
fi

# ---------------------------------------------------------------------------
# The desk
# ---------------------------------------------------------------------------
desk_json=""
if [ -z "$live_name" ]; then
	fire desk "no running ohcamel-live container to read /api/desk from"
elif ! desk_json=$(docker exec "$live_name" curl -fsS --max-time 10 http://localhost:8081/api/desk 2>&1) ||
	! printf '%s' "$desk_json" | jq -e 'type == "object"' >/dev/null 2>&1; then
	fire desk "cannot read /api/desk in $live_name: $(printf '%s' "$desk_json" | head -c 200)"
	desk_json=""
else
	recover desk
fi

if [ -n "$desk_json" ]; then
	# One jq pass: every field the checks read, stamps as epoch seconds (the
	# engine writes 2026-09-18T20:00:00.000000000Z; the fraction is dropped).
	fields=$(printf '%s' "$desk_json" | jq -r '
		def ep: if type == "string" then (sub("\\.[0-9]+"; "") | fromdateiso8601 | tostring) else "" end;
		[ (.last_error // "" | tostring),
		  (.kill_switch // "" | tostring),
		  (.last_sync | ep),
		  (.clock.is_open | tostring),
		  (.clock.source // "unknown"),
		  (.clock.read_at | ep),
		  (.clock.next_close | ep),
		  (.clock.next_close_date // "") ] | map(gsub("[\u001f\n]"; " ")) | join("\u001f")' 2>/dev/null)
	# A unit separator, not a tab: read collapses runs of whitespace IFS
	# characters, so an empty field between two tabs would vanish.
	IFS=$'\x1f' read -r last_error kill last_sync is_open source read_at next_close next_close_date <<<"$fields"
	sessions=$(printf '%s' "$desk_json" | jq -r '.recent_sessions[]?.date' 2>/dev/null)

	if [ -n "$last_error" ]; then fire last_error "/api/desk last_error: $last_error"; else recover last_error; fi
	if [ -n "$kill" ] && [ "$kill" != clear ]; then fire kill_switch "the kill switch is $kill"; else recover kill_switch; fi

	# The clock guard.
	trusted=0
	if [ "$source" != venue-clock ]; then
		say "clock.source is \"$source\"; the sync-staleness and session-row checks are skipped this run"
	elif [ -z "$read_at" ]; then
		say "clock.read_at is unreadable; the sync-staleness and session-row checks are skipped this run"
	elif [ "$read_at" -gt "$now" ]; then
		say "clock.read_at is $((read_at - now)) s in this host's future, so the reading is treated as stale; the sync-staleness and session-row checks are skipped this run"
	else
		trusted=1
	fi

	if [ "$trusted" = 1 ]; then
		# The 180 s sync.
		if [ "$is_open" = true ]; then
			if [ -z "$last_sync" ]; then
				fire sync "the market is open and the desk has never synced"
			elif [ $((now - last_sync)) -gt "$SYNC_MAX_S" ]; then
				fire sync "the market is open and last_sync is $((now - last_sync)) s old (limit ${SYNC_MAX_S} s)"
			else
				recover sync
			fi
		else
			recover sync
		fi

		# The 30-minute session rule: learn this reading's close, then check
		# every learned close that is due.
		if [ -n "$next_close_date" ] && [ -n "$next_close" ]; then
			echo "$next_close" >"$state/closes/$next_close_date"
		fi
		for f in "$state/closes"/*; do
			[ -e "$f" ] || continue
			day=${f##*/}
			close=$(cat "$f")
			if printf '%s\n' "$sessions" | grep -qx "$day"; then
				rm -f "$f"
				recover "session.$day"
			elif [ $((now - close)) -gt "$CLOSE_FORGET_S" ]; then
				say "forgetting the $day close, 7 days past with no session row"
				rm -f "$f"
			elif [ $((now - close)) -gt "$SESSION_GRACE_S" ]; then
				fire "session.$day" "no session row for $day, $(((now - close) / 60)) min after its close"
			fi
		done
	fi

	# The signal file.
	strategies=""
	if [ -r "$book" ]; then
		strategies=$(sed 's/;.*//' "$book" | tr '\n' ' ' | sed -n 's/.*(signals\(.*\)/\1/p' |
			grep -oE '\(name [a-z][a-z0-9_]{1,63}\)' | sed 's/^(name //; s/)$//')
	fi
	if [ -n "$strategies" ]; then
		owed=$(utc_date $((now - SIGNAL_LAG_S)))
		if printf '%s\n' "$sessions" | grep -qx "$owed"; then
			if listing=$(docker exec "$live_name" ls /data/signals 2>&1); then
				missing=""
				for s in $strategies; do
					printf '%s\n' "$listing" | grep -qx "$s-$owed.json" || missing="${missing:+$missing, }$s"
				done
				if [ -n "$missing" ]; then
					fire signals "no signal for $owed from: $missing"
				else
					recover signals
				fi
			else
				fire signals "cannot list /data/signals in $live_name: $(printf '%s' "$listing" | head -n 1)"
			fi
		fi
	fi
fi

# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------
newest=""
for f in "$backup_dir"/desk-*.db; do
	[ -e "$f" ] || continue
	m=$(mtime "$f")
	if [ -z "$newest" ] || [ "$m" -gt "$newest" ]; then newest=$m; fi
done
if [ -z "$newest" ]; then
	fire backup "no desk-*.db in $backup_dir"
elif [ $((now - newest)) -gt "$BACKUP_MAX_S" ]; then
	age=$((now - newest))
	fire backup "the newest desk-*.db in $backup_dir is $((age / 3600)) h $((age % 3600 / 60)) m old (limit 26 h)"
else
	recover backup
fi

pct=$(df -P "$disk_path" 2>/dev/null | awk 'NR == 2 { sub("%", "", $5); print $5 }')
if [ -z "$pct" ]; then
	say "df -P $disk_path gave no percentage; the disk check is skipped this run"
elif [ "$pct" -gt "$DISK_MAX_PCT" ]; then
	fire disk "$disk_path is ${pct}% used (limit ${DISK_MAX_PCT}%)"
else
	recover disk
fi

if [ -e "$reboot_file" ]; then
	fire reboot "$reboot_file is present: a package update wants a reboot"
else
	recover reboot
fi

# ---------------------------------------------------------------------------
# healthchecks.io
# ---------------------------------------------------------------------------
if [ -n "$hc_url" ]; then
	if [ -n "$(ls -A "$state/alerts")" ]; then
		curl_to "$hc_url/fail" --retry 2 || say "the healthchecks.io ping failed"
	else
		curl_to "$hc_url" --retry 2 || say "the healthchecks.io ping failed"
	fi
fi

exit 0
