#!/usr/bin/env bash
#
# The nightly backup: every file deploy/backup.list names, one one-shot
# container a line, plus book.sexp and deploy/.env copied beside them.
#
#   deploy/backup.sh             the run -- what ohcamel-backup.service does
#                                at 06:30 UTC (deploy/systemd/)
#   deploy/backup.sh --dry-run   print every container run and copy, start
#                                none, write nothing
#   deploy/backup.sh --check     read and validate backup.list and print its
#                                entries; no docker call of any kind
#
# What a run does, in order:
#
#   1. Reads deploy/backup.list (the line format is documented there) and
#      validates every line against deploy/docker-compose.yml's volumes before
#      anything runs. A malformed line is exit 2 with the file and line named,
#      and nothing -- not even a read-only `docker compose ps` -- has happened.
#   2. Refuses a backup directory that is missing or not writable, before any
#      container: Docker creates a missing bind-mount source as a root-owned
#      directory that uid 10001 then cannot write, and that first failure
#      would make every later one fail too. The directory is
#      /var/backups/ohcamel (OHCAMEL_BACKUP_DIR overrides it, for a test),
#      created once by the owner (finish plan O12) as
#      `install -d -m 0770 -o 10001 -g ohcamel /var/backups/ohcamel` --
#      group-writable, because three writers share it: the engine's uid
#      10001 (the journal), the Quant image's 10002 (the recorder), and the
#      host's ohcamel user (this script's book and env copies). Each
#      container runs in the invoking user's group so its copies land
#      group-readable for deploy/pull-backups.sh.
#   3. Names the engine image to run: OHCAMEL_TAG if set, else the tag the
#      running ohcamel-live container was started from, else the checkout's
#      HEAD. The backup service is the engine image at that tag
#      (docker-compose.yml, profile backup), so a backup never pulls.
#   4. Runs one `docker compose --profile backup run --rm -T` a line, as the
#      line's uid, with the line's volume at /data and the backup directory at
#      /backups, by the line's method. A line that fails does not stop the
#      others; it fails the run (exit 1). A `sqlite` source that is not in its
#      volume yet (the container exits 3) is reported and is not a failure --
#      the recorder writes its first row the first open minute after a deploy.
#   5. Copies book.sexp and deploy/.env beside the backups as
#      book-YYYY-MM-DD.sexp and deploy-YYYY-MM-DD.env at 0640, and keeps the 14
#      newest of each, as it keeps the 14 newest of each `sqlite` line's copies.
#      The journal's dailies are pruned by `ohcamel journal-backup` itself and
#      are not this script's to touch. /etc/ohcamel/live.env -- the trading
#      keys -- is never copied, never mounted, never named by a running command.
#
# OHCAMEL_TAG, OHCAMEL_BACKUP_VOLUME and OHCAMEL_BACKUP_DIR are passed to each
# docker command as a per-command prefix, never exported, so nothing here can
# leak beside the values compose reads from deploy/.env.
#
# deploy/test/backup_list_test.sh drives this file through a shim `docker` on
# PATH and pins every sentence above. deploy/restore.sh sources this file for
# engine_tag; the `[ "${BASH_SOURCE[0]}" = "$0" ]` guard at the bottom keeps a
# source from running a backup.

set -euo pipefail

backup_here=$(dirname "${BASH_SOURCE[0]}")
case "$backup_here" in
/*) ;;
*) backup_here="$PWD/$backup_here" ;;
esac

backup_say() { printf 'backup: %s\n' "$*"; }
backup_err() { printf 'backup: %s\n' "$*" >&2; }

# compose_volumes FILE: the keys under the top-level `volumes:` of a compose
# file, one a line -- read as text, so a list can be checked with no daemon.
compose_volumes() {
	awk '
		/^volumes:[[:space:]]*$/ { in_volumes = 1; next }
		in_volumes && /^[^[:space:]#]/ { in_volumes = 0 }
		in_volumes && /^  [A-Za-z0-9_.-]+:/ { sub(/^  /, ""); sub(/:.*/, ""); print }
	' "$1"
}

# engine_tag COMPOSE_FILE CHECKOUT_ROOT: the image tag the backup service
# runs, on stdout. OHCAMEL_TAG wins; then the image the running ohcamel-live
# container was created from (so a backup runs the engine that is deployed,
# which is an image the host already holds); then the checkout's HEAD, which
# deploy.sh leaves at the deployed sha. None of them: exit 1, naming
# OHCAMEL_TAG, because guessing an image is how a backup runs a build nobody
# chose.
engine_tag() {
	local compose="$1" root="$2" image sha
	if [ -n "${OHCAMEL_TAG:-}" ]; then
		printf '%s\n' "$OHCAMEL_TAG"
		return 0
	fi
	image=$(docker compose -f "$compose" --profile live ps --format '{{.Image}}' ohcamel-live 2>/dev/null | head -n 1) || image=""
	if [ -n "$image" ]; then
		printf '%s\n' "${image##*:}"
		return 0
	fi
	if sha=$(git -C "$root" rev-parse HEAD 2>/dev/null) && [ -n "$sha" ]; then
		printf '%s\n' "$sha"
		return 0
	fi
	backup_err "cannot tell which engine image to run: OHCAMEL_TAG is unset, no ohcamel-live container is running, and $root is not a git checkout -- pass OHCAMEL_TAG=<sha>"
	return 1
}

# The `sqlite` method, run by /bin/sh inside the engine image (dash: no
# bashisms) as `sh -ec SCRIPT sqlite-backup SRC DIR DEST`. The sqlite3 CLI is
# installed in the runtime image for exactly this (deploy/Dockerfile).
#
#   - `-readonly` opens SRC with SQLITE_OPEN_READONLY, so the source is only
#     ever read; a WAL-mode source still needs its -shm created beside it,
#     which is why the container runs as the volume's owning uid.
#   - `.backup` is SQLite's online backup API from the CLI: a consistent copy
#     of SRC, writer or no writer, into DEST.part.
#   - The copy is reopened, switched to a rollback journal so it is one
#     self-contained file (a WAL header on a copy makes a later read-only open
#     want a -shm, the trap Task 12 found for the journal), and
#     integrity_checked; only an `ok` lets it take its name. Anything else
#     exits 1, and the EXIT trap removes the .part either way -- `.backup`
#     creates the destination before it reads the source, so a source that
#     is not a database would otherwise leave an empty .part behind on every
#     run -- so a bad copy never holds one of the 14 places retention keeps.
#   - A SRC that does not exist exits 3, which backup.sh reports as absent and
#     does not count as a failure.
sqlite_backup_script='set -eu
umask 027
src=$1
dir=$2
dest=$3
[ -f "$src" ] || exit 3
part="$dir/$dest.part"
rm -f -- "$part" "$part-journal"
cleanup() { rm -f -- "$part" "$part-journal"; }
trap cleanup EXIT
sqlite3 -readonly "$src" ".backup \"$part\""
verdict=$(sqlite3 "$part" "PRAGMA journal_mode=DELETE; PRAGMA integrity_check;" | tail -n 1)
if [ "$verdict" != ok ]; then
	echo "backup: integrity_check on the copy of $src answered: $verdict" >&2
	exit 1
fi
mv -- "$part" "$dir/$dest"
'

# --- the list ------------------------------------------------------------------

# Filled by read_list, one element a list entry, in file order.
entry_line=()
entry_volume=()
entry_path=()
entry_method=()
entry_uid=()
entry_dest=()
entry_stem=()
entry_ext=()

# refuse LIST LINENO MESSAGE: a malformed line. Exit 2 before anything runs.
refuse() {
	printf '%s:%s: %s\n' "$1" "$2" "$3" >&2
	exit 2
}

# read_list LIST COMPOSE_FILE TODAY: validates every line, or exits 2.
read_list() {
	local list="$1" compose="$2" today="$3"
	local volumes raw line lineno=0 n=0 journal_at="" i
	local volume path method uid base stem ext dest
	if [ ! -f "$list" ]; then
		printf '%s: missing -- the nightly backup needs the list of what to copy\n' "$list" >&2
		exit 2
	fi
	volumes=$(compose_volumes "$compose" | tr '\n' ' ')
	while IFS= read -r raw || [ -n "$raw" ]; do
		lineno=$((lineno + 1))
		line=${raw#"${raw%%[![:space:]]*}"}
		case "$line" in
		'' | '#'*) continue ;;
		esac
		set -f
		# shellcheck disable=SC2086 # word-splitting the line into its fields is the point
		set -- $line
		set +f
		if [ $# -ne 4 ]; then
			refuse "$list" "$lineno" "expected four fields VOLUME PATH METHOD UID, got $#: '$line'"
		fi
		volume=$1 path=$2 method=$3 uid=$4
		case " $volumes " in
		*" $volume "*) ;;
		*) refuse "$list" "$lineno" "volume '$volume' is not declared under volumes: in $compose (declared:$volumes)" ;;
		esac
		case "$path" in
		/data/*) ;;
		*) refuse "$list" "$lineno" "PATH '$path' must be an absolute path under /data/, where every service mounts its volume" ;;
		esac
		case "/$path/" in
		*/../*) refuse "$list" "$lineno" "PATH '$path' climbs out of the volume with .." ;;
		esac
		case "$path" in
		*/) refuse "$list" "$lineno" "PATH '$path' names a directory, not a file" ;;
		esac
		case "$uid" in
		'' | *[!0-9]*) refuse "$list" "$lineno" "UID '$uid' is not a number; it is the uid of the image that owns $volume (10001 for the engine, 10002 for Quant)" ;;
		esac
		if [ "$uid" -eq 0 ]; then
			refuse "$list" "$lineno" "UID 0 is root; no backup runs as root -- name the uid that owns $volume"
		fi
		base=${path##*/}
		case "$method" in
		journal)
			if [ -n "$journal_at" ]; then
				refuse "$list" "$lineno" "a second journal line; the journal method always writes desk-YYYY-MM-DD.db, and line $journal_at already owns that name"
			fi
			journal_at=$lineno
			stem=desk
			ext=db
			dest="desk-$today.db"
			;;
		sqlite)
			case "$base" in
			*.*) ;;
			*) refuse "$list" "$lineno" "'$base' has no extension; a sqlite copy is named STEM-YYYY-MM-DD.EXT" ;;
			esac
			stem=${base%.*}
			ext=${base##*.}
			case "$stem" in
			'' | *[!A-Za-z0-9_]*) refuse "$list" "$lineno" "'$base': the name before the extension must be letters, digits and underscores (it becomes the copy's STEM-YYYY-MM-DD prefix)" ;;
			esac
			case "$ext" in
			'' | *[!A-Za-z0-9]*) refuse "$list" "$lineno" "'$base': the extension must be letters and digits" ;;
			esac
			case "$stem" in
			desk | pre-deploy) refuse "$list" "$lineno" "'$base' would be copied as $stem-YYYY-MM-DD.$ext, a name 'ohcamel journal-backup' prunes as its own (desk-*.db, pre-deploy-*.db); use the journal method, or another file name" ;;
			esac
			dest="$stem-$today.$ext"
			;;
		duckdb)
			refuse "$list" "$lineno" "method 'duckdb' is reserved for Lane C (CHECKPOINT, then a file copy) and is not implemented yet"
			;;
		*)
			refuse "$list" "$lineno" "unknown method '$method' -- journal or sqlite (duckdb is reserved for Lane C)"
			;;
		esac
		i=0
		while [ "$i" -lt "$n" ]; do
			if [ "${entry_dest[$i]}" = "$dest" ]; then
				refuse "$list" "$lineno" "would write $dest, the same file as line ${entry_line[$i]}"
			fi
			i=$((i + 1))
		done
		entry_line[$n]=$lineno
		entry_volume[$n]=$volume
		entry_path[$n]=$path
		entry_method[$n]=$method
		entry_uid[$n]=$uid
		entry_dest[$n]=$dest
		entry_stem[$n]=$stem
		entry_ext[$n]=$ext
		n=$((n + 1))
	done <"$list"
	if [ "$n" -eq 0 ]; then
		printf '%s: no entry -- a list with nothing in it would exit 0 having backed up nothing, every night\n' "$list" >&2
		exit 2
	fi
}

# --- the run -------------------------------------------------------------------

# quoted ARG...: the command as one shell-quoted line, for --dry-run.
quoted() {
	local out="" a
	for a in "$@"; do
		out="$out$(printf '%q' "$a") "
	done
	printf '%s' "${out% }"
}

# prune_dated DIR STEM EXT: keep the 14 newest DIR/STEM-YYYY-MM-DD.EXT by the
# date in the name, delete the rest, and delete any STEM-DATE.EXT.part or
# .part-journal a killed run left behind. Only names of exactly that shape:
# notes.txt, recorder-notes.sqlite and another entry's copies are not this
# entry's to judge.
prune_dated() {
	local dir="$1" stem="$2" ext="$3" name
	find "$dir" -maxdepth 1 -name "$stem-*.$ext.part*" | sed 's|.*/||' | while IFS= read -r name; do
		case "$name" in
		"$stem"-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]."$ext".part | "$stem"-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]."$ext".part-journal)
			rm -f -- "$dir/$name"
			backup_say "removed the leftover $name"
			;;
		esac
	done
	# A date sorts as text, so `sort -r` is newest first and everything past
	# the 14th line is older than the 14 kept. `tail -n +15` is POSIX; GNU
	# `head -n -14` is not.
	find "$dir" -maxdepth 1 -name "$stem-*.$ext" | sed 's|.*/||' |
		grep -E "^$stem-[0-9]{4}-[0-9]{2}-[0-9]{2}\.$ext\$" | sort -r | tail -n +15 |
		while IFS= read -r name; do
			rm -f -- "$dir/$name"
			backup_say "pruned $name (the 14 newest $stem copies are kept)"
		done
}

# copy_config SRC DIR NAME STEM EXT DRY: book.sexp or deploy/.env beside the
# backups at 0640, then the 14 newest of that stem.
copy_config() {
	local src="$1" dir="$2" name="$3" stem="$4" ext="$5" dry="$6"
	if [ "$dry" = 1 ]; then
		printf '+ cp -- %s %s/%s\n' "$(printf '%q' "$src")" "$(printf '%q' "$dir")" "$name"
		return 0
	fi
	if [ ! -f "$src" ]; then
		backup_err "$src is missing -- not copied"
		return 1
	fi
	if (umask 077 && cp -- "$src" "$dir/$name.part") && chmod 0640 "$dir/$name.part" && mv -- "$dir/$name.part" "$dir/$name"; then
		backup_say "copied $src -> $dir/$name"
		prune_dated "$dir" "$stem" "$ext"
		return 0
	fi
	backup_err "could not copy $src to $dir/$name"
	rm -f -- "$dir/$name.part"
	return 1
}

# run_entry INDEX COMPOSE_FILE TAG DIR GID DRY: one list line, one container.
run_entry() {
	local i="$1" compose="$2" tag="$3" dir="$4" gid="$5" dry="$6"
	local volume="${entry_volume[$i]}" path="${entry_path[$i]}" method="${entry_method[$i]}"
	local uid="${entry_uid[$i]}" dest="${entry_dest[$i]}" rc=0
	local cmd=(docker compose -f "$compose" --profile backup run --rm -T --user "$uid:$gid")
	case "$method" in
	journal) cmd+=(ohcamel-backup journal-backup "$path" /backups) ;;
	sqlite) cmd+=(--entrypoint sh ohcamel-backup -ec "$sqlite_backup_script" sqlite-backup "$path" /backups "$dest") ;;
	esac
	if [ "$dry" = 1 ]; then
		printf '+ OHCAMEL_TAG=%s OHCAMEL_BACKUP_VOLUME=%s OHCAMEL_BACKUP_DIR=%s %s\n' \
			"$(printf '%q' "$tag")" "$volume" "$(printf '%q' "$dir")" "$(quoted "${cmd[@]}")"
		return 0
	fi
	backup_say "$volume:$path ($method, as uid $uid) -> $dir/$dest"
	OHCAMEL_TAG="$tag" OHCAMEL_BACKUP_VOLUME="$volume" OHCAMEL_BACKUP_DIR="$dir" "${cmd[@]}" || rc=$?
	case "$method:$rc" in
	*:0)
		backup_say "$volume:$path -> $dest ok"
		if [ "$method" = sqlite ]; then
			prune_dated "$dir" "${entry_stem[$i]}" "${entry_ext[$i]}"
		fi
		return 0
		;;
	sqlite:3)
		backup_say "$volume:$path is absent in the volume -- nothing to copy yet (the recorder writes its first row the first open minute after a deploy); not a failure"
		return 0
		;;
	*)
		backup_err "$volume:$path ($method) FAILED, exit $rc -- today's $dest is not to be trusted"
		return 1
		;;
	esac
}

usage() {
	cat >&2 <<'EOF'
usage: deploy/backup.sh [--dry-run | --check]

  (no flag)   back up every file deploy/backup.list names, plus book.sexp and
              deploy/.env, into /var/backups/ohcamel (OHCAMEL_BACKUP_DIR)
  --dry-run   print every container run and copy; start nothing, write nothing
  --check     read and validate deploy/backup.list and print its entries; no
              docker call of any kind
EOF
	exit 2
}

backup_main() {
	local dry=0 check=0 arg
	for arg in "$@"; do
		case "$arg" in
		--dry-run) dry=1 ;;
		--check) check=1 ;;
		*) usage ;;
		esac
	done
	if [ "$dry" = 1 ] && [ "$check" = 1 ]; then usage; fi

	local compose="$backup_here/docker-compose.yml" list="$backup_here/backup.list" root="$backup_here/.."
	local dir="${OHCAMEL_BACKUP_DIR:-/var/backups/ohcamel}"
	# BACKUP_TODAY pins the date for a test; the timer's today is UTC, like
	# `ohcamel journal-backup`'s.
	local today="${BACKUP_TODAY:-$(date -u +%F)}"
	local i n tag gid failures=0

	read_list "$list" "$compose" "$today"
	n=${#entry_line[@]}

	if [ "$check" = 1 ]; then
		backup_say "$list: $n entr$([ "$n" = 1 ] && echo y || echo ies), every line well formed"
		i=0
		while [ "$i" -lt "$n" ]; do
			backup_say "  line ${entry_line[$i]}: ${entry_volume[$i]}:${entry_path[$i]} by ${entry_method[$i]} as uid ${entry_uid[$i]} -> ${entry_dest[$i]}"
			i=$((i + 1))
		done
		return 0
	fi

	if [ ! -d "$dir" ]; then
		backup_err "$dir does not exist; nothing runs until it does. Create it once, group-writable for the three uids that write into it: install -d -m 0770 -o 10001 -g ohcamel $dir"
		return 1
	fi
	if [ ! -w "$dir" ]; then
		backup_err "$dir is not writable by $(id -un) (uid $(id -u), gid $(id -g)); nothing runs. It must be group-writable, because uid 10001 writes the journal's copies, uid 10002 the recorder's and this user the book's: install -d -m 0770 -o 10001 -g ohcamel $dir"
		return 1
	fi

	tag=$(engine_tag "$compose" "$root") || return 1
	gid=$(id -g)
	if [ "$dry" = 1 ]; then
		backup_say "dry run -- $n entr$([ "$n" = 1 ] && echo y || echo ies), engine image tag $tag, into $dir; nothing below is run"
	else
		backup_say "$today -- $n entr$([ "$n" = 1 ] && echo y || echo ies), engine image tag $tag, into $dir"
	fi

	i=0
	while [ "$i" -lt "$n" ]; do
		run_entry "$i" "$compose" "$tag" "$dir" "$gid" "$dry" || failures=$((failures + 1))
		i=$((i + 1))
	done
	copy_config "$root/book.sexp" "$dir" "book-$today.sexp" book sexp "$dry" || failures=$((failures + 1))
	copy_config "$backup_here/.env" "$dir" "deploy-$today.env" deploy env "$dry" || failures=$((failures + 1))

	if [ "$failures" -ne 0 ]; then
		backup_err "$failures of $((n + 2)) steps FAILED (above)"
		return 1
	fi
	if [ "$dry" = 0 ]; then
		backup_say "done: $n copies and the two configuration files, in $dir"
	fi
	return 0
}

# Guarded so deploy/restore.sh can `source` this file for engine_tag without
# running a backup -- $0 is the invoking script when sourced, but
# ${BASH_SOURCE[0]} is always this file.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	backup_main "$@"
fi
