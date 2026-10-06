#!/usr/bin/env bash
#
# Drives deploy/backup.sh through a shim `docker` on PATH and pins what the
# nightly backup does with deploy/backup.list:
#
#   deploy/test/backup_list_test.sh
#
# Exits non-zero, naming the assertion, if any case disagrees with the
# hand-derived expectation below. Needs bash, git and coreutils; no daemon, no
# image, no container, no network. backup.sh is run from a scratch copy of the
# three files it reads (itself, backup.list, docker-compose.yml) beside a FAKE
# book.sexp and a FAKE deploy/.env written here, so the real deploy/.env is
# never opened by this test, and /etc/ohcamel is never named.
#
# Why this exists. The backup is a systemd timer on a host the agent cannot
# reach, running containers nobody watches at 06:30 UTC. Everything it can get
# wrong is silent until the day a restore is needed: a list line that runs no
# container, a container on the wrong volume or as a uid that cannot open a
# WAL database, a typo in the list that stops the script after the journal was
# copied but before the recorder was, a copy of the book that was never made,
# or a credential copied beside the backups. Each is a case here:
#
#   L. the committed list names the three files owed so far -- the desk's
#      journal, the Flight Deck's recorder and the job queue -- at the path, by the method
#      and as the uid the owning image really uses.
#   A. each list line becomes EXACTLY ONE container run, on that line's
#      volume, reading that line's path, by that line's method, as that
#      line's uid; the image tag is the running engine's; book.sexp and
#      deploy/.env are copied beside the backups, and live.env never is.
#   B. a malformed line fails the run BEFORE anything runs: exit 2, the file
#      and line named, no docker call of any kind, nothing written.
#   C. one line failing does not stop the others, and fails the run; a source
#      that does not exist yet (exit 3 from the container) is reported and is
#      not a failure.
#   D. retention of what backup.sh itself writes: the 14 newest of each.
#   E. --dry-run and --check run no container and write nothing.
#   F. a missing backup directory is refused before any container runs.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=deploy/backup.sh
list=deploy/backup.list
compose=deploy/docker-compose.yml

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

missing=0
for f in "$script" "$list" "$compose" deploy/Dockerfile quant/Dockerfile \
	quant/src/ohcamel_quant/api/routers/deck.py quant/src/ohcamel_quant/jobs/paths.py; do
	if [ ! -f "$f" ]; then
		no "$f" "missing"
		missing=1
	fi
done
# Nothing below can run without the script and its list; say so once and stop.
if [ "$missing" = 1 ]; then finish; fi

scratch=$(mktemp -d "${TMPDIR:-/tmp}/backup-list-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

# The day every case runs on, so every expected file name is a constant.
today=2026-09-27
gid=$(id -g)
log="$scratch/docker.log"
backups="$scratch/backups"
repo="$scratch/repo"

# --- the shim -----------------------------------------------------------------
# One line per invocation: the three variables backup.sh passes per command,
# the compose verb, then every argument shell-quoted (so a multi-line script
# stays on its line). A `run` is simulated by creating the file the real
# container would have written, or by exiting with the status a case asks for.
mkdir -p "$scratch/bin"
cat >"$scratch/bin/docker" <<'SHIM'
#!/usr/bin/env bash
set -u
verb=""
skip=0
for a in "$@"; do
	if [ "$skip" = 1 ]; then skip=0; continue; fi
	case "$a" in
	compose) ;;
	-f | --file | --profile | -p | --project-name | --env-file) skip=1 ;;
	-*) ;;
	*) verb="$a"; break ;;
	esac
done
{
	printf 'verb=%s tag=%s volume=%s dir=%s ::' "$verb" "${OHCAMEL_TAG-}" \
		"${OHCAMEL_BACKUP_VOLUME-}" "${OHCAMEL_BACKUP_DIR-}"
	printf ' %q' "$@"
	printf '\n'
} >>"$SHIM_LOG"
case "$verb" in
ps)
	[ -n "${SHIM_PS_IMAGE-}" ] && printf '%s\n' "$SHIM_PS_IMAGE"
	exit 0
	;;
run)
	case " $* " in
	*" journal-backup "*)
		[ "${SHIM_EXIT_JOURNAL:-0}" = 0 ] || exit "$SHIM_EXIT_JOURNAL"
		: >"$OHCAMEL_BACKUP_DIR/desk-$SHIM_TODAY.db"
		;;
	*)
		[ "${SHIM_EXIT_SQLITE:-0}" = 0 ] || exit "$SHIM_EXIT_SQLITE"
		# The destination's name is the run's last argument.
		: >"$OHCAMEL_BACKUP_DIR/${!#}"
		;;
	esac
	exit 0
	;;
esac
exit 0
SHIM
chmod +x "$scratch/bin/docker"

# fresh: a new scratch checkout, an empty backup directory, an empty log.
fresh() {
	rm -rf "$repo" "$backups"
	mkdir -p "$repo/deploy" "$backups"
	cp "$script" "$repo/deploy/backup.sh"
	cp "$list" "$repo/deploy/backup.list"
	cp "$compose" "$repo/deploy/docker-compose.yml"
	printf '%s\n' '((book backup-test-fixture))' >"$repo/book.sexp"
	printf '%s\n' 'OHCAMEL_DEMO_HOST=backup-test.invalid' >"$repo/deploy/.env"
	: >"$log"
}

# run_backup [VAR=value ...] -- [backup.sh arguments ...]
# stdout to $scratch/out, stderr to $scratch/err, the status in $rc. The
# caller's own OHCAMEL_* are deliberately not inherited.
rc=0
run_backup() {
	local envs=()
	while [ $# -gt 0 ] && [ "$1" != "--" ]; do
		envs+=("$1")
		shift
	done
	[ $# -gt 0 ] && shift
	rc=0
	env -u OHCAMEL_TAG -u OHCAMEL_IMAGE -u OHCAMEL_BACKUP_VOLUME -u OHCAMEL_BACKUP_LIST \
		-u COMPOSE_PROJECT_NAME \
		PATH="$scratch/bin:$PATH" SHIM_LOG="$log" SHIM_TODAY="$today" \
		OHCAMEL_BACKUP_DIR="$backups" BACKUP_TODAY="$today" \
		SHIM_PS_IMAGE="ghcr.io/ajaiupadhyaya/ohcamel:cafe1234" \
		${envs[@]+"${envs[@]}"} \
		bash "$repo/deploy/backup.sh" "$@" >"$scratch/out" 2>"$scratch/err" || rc=$?
}

calls() { grep -c . "$log" || true; }
runs() { grep -c '^verb=run ' "$log" || true; }
run_line() { grep '^verb=run ' "$log" | sed -n "${1}p"; }
listing() { (cd "$backups" && find . -mindepth 1 | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//'); }
mode_of() { find "$1" -maxdepth 0 -exec ls -ld {} \; | cut -c1-10; }

expect_rc() {
	if [ "$rc" -eq "$2" ]; then
		ok "$1"
	else
		no "$1" "exit $rc, expected $2; stderr: $(head -c 300 "$scratch/err" | tr '\n' ' ')"
	fi
}
expect_eq() {
	if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "got '$2', expected '$3'"; fi
}
expect_has() { # LABEL FILE FIXED-STRING
	if grep -qF -- "$3" "$2"; then ok "$1"; else no "$1" "'$3' not found in: $(head -c 300 "$2" | tr '\n' ' ')"; fi
}

# --- L. the committed list ----------------------------------------------------
# An entry is a line that is neither blank nor a comment, its fields squeezed
# to single spaces. Read here with grep and awk, independently of backup.sh.
entries=$(grep -vE '^[[:space:]]*(#|$)' "$list" | awk '{$1=$1}1' || true)
n_entries=$(printf '%s\n' "$entries" | grep -c . || true)

for want in 'desk_data /data/desk.db journal 10001' \
	'quant_data /data/deck/recorder.sqlite sqlite 10002' \
	'quant_data /data/jobs.sqlite sqlite 10002'; do
	if printf '%s\n' "$entries" | grep -qxF -- "$want"; then
		ok "L1 backup.list carries '$want'"
	else
		no "L1 backup.list carries '$want'" "entries are: $(printf '%s' "$entries" | tr '\n' ';')"
	fi
done
# The journal's path is the one ohcamel-live is told to write, and the
# recorder's is the one the Quant app builds from its data directory, which
# compose sets to /data on the quant_data volume.
if grep -qE '^ +OHCAMEL_JOURNAL: /data/desk\.db$' "$compose"; then
	ok "L2 compose gives the live engine OHCAMEL_JOURNAL=/data/desk.db"
else
	no "L2 compose gives the live engine OHCAMEL_JOURNAL=/data/desk.db" "not found in $compose"
fi
if grep -qF 'Path(settings.data_dir) / "deck" / "recorder.sqlite"' quant/src/ohcamel_quant/api/routers/deck.py &&
	grep -qE '^ +OHCAMEL_QUANT_DATA_DIR: /data$' "$compose"; then
	ok "L2 the Quant app keeps its recorder at {data_dir}/deck/recorder.sqlite, data_dir=/data"
else
	no "L2 the Quant app keeps its recorder at {data_dir}/deck/recorder.sqlite, data_dir=/data" "deck.py or compose no longer says so"
fi
if grep -qF 'return Path((settings or get_settings()).data_dir) / "jobs.sqlite"' quant/src/ohcamel_quant/jobs/paths.py &&
	grep -qE '^ +OHCAMEL_QUANT_DATA_DIR: /data$' "$compose"; then
	ok "L2 the job system keeps jobs.sqlite at {data_dir}/jobs.sqlite, data_dir=/data"
else
	no "L2 the job system keeps jobs.sqlite at {data_dir}/jobs.sqlite, data_dir=/data" "paths.py or compose no longer says so"
fi
# The uid in the list is the uid the owning image runs as -- a WAL reader
# creates the -shm beside the database, which only the directory's owner can.
if grep -qE '^RUN useradd --system --uid 10001 ' deploy/Dockerfile; then
	ok "L3 the engine image's user is uid 10001 (owns desk_data)"
else
	no "L3 the engine image's user is uid 10001 (owns desk_data)" "deploy/Dockerfile's useradd line moved"
fi
if grep -qE '^RUN useradd --system --uid 10002 ' quant/Dockerfile; then
	ok "L3 the Quant image's user is uid 10002 (owns quant_data)"
else
	no "L3 the Quant image's user is uid 10002 (owns quant_data)" "quant/Dockerfile's useradd line moved"
fi

# --- A. the committed list, through the shim ------------------------------------
fresh
run_backup --
expect_rc "A1 the committed list backs up cleanly" 0
# One run a line: the list has n_entries entries (3 today: the journal, the
# recorder and the job queue), so n_entries runs -- no more, no fewer.
expect_eq "A2 $n_entries list lines became exactly $n_entries container runs" "$(runs)" "$n_entries"

i=0
while IFS=' ' read -r volume path method uid; do
	[ -n "$volume" ] || continue
	i=$((i + 1))
	line=$(run_line "$i")
	label="A3 line $i ($volume $path $method $uid)"
	case "$line" in
	*" volume=$volume "*) ok "$label runs on volume $volume" ;;
	*) no "$label runs on volume $volume" "run $i was: $(printf '%s' "$line" | cut -c1-200)" ;;
	esac
	case "$line" in
	*" --user $uid:$gid "*) ok "$label runs as $uid, in the invoking user's group ($gid)" ;;
	*) no "$label runs as $uid:$gid" "run $i was: $(printf '%s' "$line" | cut -c1-300)" ;;
	esac
	case "$method" in
	journal)
		# The engine's own command, with no shell between compose and it.
		case "$line" in
		*" ohcamel-backup journal-backup $path /backups") ok "$label is 'ohcamel journal-backup $path /backups'" ;;
		*) no "$label is 'ohcamel journal-backup $path /backups'" "run $i was: $(printf '%s' "$line" | cut -c1-300)" ;;
		esac
		case "$line" in
		*--entrypoint*) no "$label keeps the image's entrypoint" "an --entrypoint was passed" ;;
		*) ok "$label keeps the image's entrypoint" ;;
		esac
		;;
	sqlite)
		base=${path##*/}
		dest="${base%.*}-$today.${base##*.}"
		case "$line" in
		*" --entrypoint sh "*"ohcamel-backup -ec "*" $path /backups $dest") ok "$label reads $path and writes /backups/$dest" ;;
		*) no "$label reads $path and writes /backups/$dest" "run $i was: $(printf '%s' "$line" | cut -c1-200) ... $(printf '%s' "$line" | rev | cut -c1-120 | rev)" ;;
		esac
		# The method itself: SQLite's online backup through the CLI, on a
		# read-only handle, and the copy checked before it takes its name.
		for word in 'sqlite3 -readonly' '.backup' 'integrity_check'; do
			case "$line" in
			*"$word"*) ok "$label uses $word" ;;
			*) no "$label uses $word" "not in the script passed to the container" ;;
			esac
		done
		;;
	*) no "$label" "this test does not know the method '$method' -- teach it" ;;
	esac
	for word in ' --profile backup ' ' run ' ' --rm ' ' -T ' " -f $repo/deploy/docker-compose.yml "; do
		case "$line" in
		*"$word"*) ;;
		*) no "A4 run $i passes$word" "run $i was: $(printf '%s' "$line" | cut -c1-300)" ;;
		esac
	done
	case "$line" in
	*" tag=cafe1234 "*" dir=$backups "*) ok "A4 run $i: the running engine's tag, the backup directory, profile backup, --rm, -T" ;;
	*) no "A4 run $i: the running engine's tag and the backup directory" "run $i began: $(printf '%s' "$line" | cut -c1-200)" ;;
	esac
done <<EOF
$entries
EOF

# What is in the directory afterwards: the three files the (simulated)
# containers wrote and the two backup.sh copies itself. 3 + 2 = 5.
expect_eq "A5 the directory holds the three copies and the two configuration files" "$(listing)" \
	"book-$today.sexp deploy-$today.env desk-$today.db jobs-$today.sqlite recorder-$today.sqlite"
if cmp -s "$repo/book.sexp" "$backups/book-$today.sexp"; then
	ok "A6 book-$today.sexp is book.sexp, byte for byte"
else
	no "A6 book-$today.sexp is book.sexp, byte for byte" "differs or missing"
fi
if cmp -s "$repo/deploy/.env" "$backups/deploy-$today.env"; then
	ok "A6 deploy-$today.env is deploy/.env, byte for byte"
else
	no "A6 deploy-$today.env is deploy/.env, byte for byte" "differs or missing"
fi
for f in "book-$today.sexp" "deploy-$today.env"; do
	if [ -e "$backups/$f" ]; then
		expect_eq "A6 $f is 0640" "$(mode_of "$backups/$f")" "-rw-r-----"
	else
		no "A6 $f is 0640" "missing"
	fi
done
# live.env holds the trading keys. It is never copied, never mounted, never named.
if grep -q '/etc/ohcamel' "$log"; then
	no "A7 no docker call names /etc/ohcamel" "$(grep -c '/etc/ohcamel' "$log") call(s) do"
else
	ok "A7 no docker call names /etc/ohcamel"
fi
if grep -vE '^[[:space:]]*#' "$script" | grep -qE 'live\.env|/etc/ohcamel'; then
	no "A7 backup.sh names neither live.env nor /etc/ohcamel outside a comment" "$(grep -vnE '^[[:space:]]*#' "$script" | grep -E 'live\.env|/etc/ohcamel' | head -3 | tr '\n' ' ')"
else
	ok "A7 backup.sh names neither live.env nor /etc/ohcamel outside a comment"
fi

# The tag. Three sources, in this order: OHCAMEL_TAG, the image the engine's
# container runs, the checkout's HEAD.
fresh
run_backup OHCAMEL_TAG=feedface --
expect_rc "A8 OHCAMEL_TAG given" 0
expect_eq "A8 an explicit OHCAMEL_TAG wins over the running container's" \
	"$(grep -c '^verb=run tag=feedface ' "$log" || true)" "$n_entries"

fresh
git -C "$repo" init -q
git -C "$repo" -c user.email=test@example.invalid -c user.name=test commit -q --allow-empty -m fixture
head_sha=$(git -C "$repo" rev-parse HEAD)
run_backup SHIM_PS_IMAGE= --
expect_rc "A9 no engine container: the checkout's HEAD names the image" 0
expect_eq "A9 with no engine container the tag is the checkout's HEAD" \
	"$(grep -c "^verb=run tag=$head_sha " "$log" || true)" "$n_entries"

fresh
run_backup SHIM_PS_IMAGE= GIT_CEILING_DIRECTORIES="$scratch" --
expect_rc "A10 no tag, no container, no checkout: refused" 1
expect_eq "A10 and nothing ran" "$(runs)" "0"
expect_has "A10 the refusal names OHCAMEL_TAG" "$scratch/err" "OHCAMEL_TAG"

# --- B. a malformed list fails before anything runs ------------------------------
# malformed LABEL LINE-NUMBER WORD < list
# Exit 2; stderr names backup.list:LINE and carries WORD; no docker call of
# any kind (not even the read-only `ps`); nothing in the backup directory.
malformed() {
	local label="$1" lineno="$2" word="$3"
	fresh
	cat >"$repo/deploy/backup.list"
	run_backup --
	if [ "$rc" -eq 2 ] && [ "$(calls)" = 0 ] && [ -z "$(listing)" ] &&
		grep -q "backup.list:$lineno: " "$scratch/err" && grep -qF -- "$word" "$scratch/err"; then
		ok "$label"
	else
		no "$label" "exit $rc (want 2), $(calls) docker call(s) (want 0), directory '$(listing)' (want empty), stderr: $(head -c 300 "$scratch/err" | tr '\n' ' ')"
	fi
}

malformed "B1 three fields (no uid) is refused" 1 "four fields" <<'EOF'
quant_data /data/deck/recorder.sqlite sqlite
EOF
malformed "B2 five fields is refused" 1 "four fields" <<'EOF'
quant_data /data/deck/recorder.sqlite sqlite 10002 nightly
EOF
malformed "B3 an unknown method is refused" 1 "rsync" <<'EOF'
quant_data /data/deck/recorder.sqlite rsync 10002
EOF
malformed "B4 duckdb is reserved for Lane C, and says so" 1 "reserved" <<'EOF'
quant_data /data/warehouse.duckdb duckdb 10002
EOF
malformed "B5 a relative path is refused" 1 "/data/" <<'EOF'
quant_data deck/recorder.sqlite sqlite 10002
EOF
malformed "B6 a path outside /data is refused" 1 "/data/" <<'EOF'
quant_data /etc/passwd sqlite 10002
EOF
malformed "B7 a path that climbs out with .. is refused" 1 ".." <<'EOF'
quant_data /data/../etc/passwd sqlite 10002
EOF
malformed "B8 a volume compose does not declare is refused" 1 "warehouse_data" <<'EOF'
warehouse_data /data/jobs.sqlite sqlite 10002
EOF
malformed "B9 a uid that is not a number is refused" 1 "quant" <<'EOF'
quant_data /data/deck/recorder.sqlite sqlite quant
EOF
malformed "B10 uid 0 is refused: no backup runs as root" 1 "root" <<'EOF'
quant_data /data/deck/recorder.sqlite sqlite 0
EOF
malformed "B11 two lines that would write the same file are refused" 2 "recorder-" <<'EOF'
quant_data /data/deck/recorder.sqlite sqlite 10002
quant_data /data/other/recorder.sqlite sqlite 10002
EOF
malformed "B12 two journal lines are refused: both would write desk-DATE.db" 2 "journal" <<'EOF'
desk_data /data/desk.db journal 10001
desk_data /data/desk2.db journal 10001
EOF
malformed "B13 a sqlite copy that would take a journal daily's name is refused" 1 "desk-" <<'EOF'
quant_data /data/desk.db sqlite 10002
EOF
# The case the requirement is about: two good lines FIRST, the bad one last,
# comments and blank lines counted, so the bad line is line 6 of the file --
# and still neither good line ran.
malformed "B14 a bad line after two good ones: line 6 is named and nothing ran" 6 "four fields" <<'EOF'
# the journal
desk_data /data/desk.db journal 10001

# the recorder
quant_data /data/deck/recorder.sqlite sqlite 10002
quant_data /data/jobs.sqlite sqlite
EOF
# An empty list would exit 0 having backed up nothing, every night.
fresh
printf '%s\n' '# nothing' '' >"$repo/deploy/backup.list"
run_backup --
if [ "$rc" -eq 2 ] && [ "$(calls)" = 0 ] && [ -z "$(listing)" ]; then
	ok "B15 a list with no entry is refused"
else
	no "B15 a list with no entry is refused" "exit $rc, $(calls) docker call(s), directory '$(listing)'"
fi
fresh
rm "$repo/deploy/backup.list"
run_backup --
if [ "$rc" -eq 2 ] && [ "$(calls)" = 0 ] && [ -z "$(listing)" ]; then
	ok "B16 a missing list is refused"
else
	no "B16 a missing list is refused" "exit $rc, $(calls) docker call(s), directory '$(listing)'"
fi

# --- C. one line's failure ---------------------------------------------------------
fresh
run_backup SHIM_EXIT_SQLITE=1 --
expect_rc "C1 the recorder's copy failing fails the run" 1
expect_eq "C1 and every line still ran" "$(runs)" "$n_entries"
expect_eq "C1 and the journal and the configuration were still copied" "$(listing)" \
	"book-$today.sexp deploy-$today.env desk-$today.db"

fresh
run_backup SHIM_EXIT_JOURNAL=1 --
expect_rc "C2 the journal's copy failing fails the run" 1
expect_eq "C2 and every line still ran" "$(runs)" "$n_entries"
expect_eq "C2 and the recorder, the job queue and the configuration were still copied" "$(listing)" \
	"book-$today.sexp deploy-$today.env jobs-$today.sqlite recorder-$today.sqlite"

# Exit 3 is the container saying "the file is not in the volume": the recorder
# writes its first row the first minute a US session is open, so a host
# deployed on a Saturday has no recorder.sqlite on Sunday's 06:30 run.
fresh
run_backup SHIM_EXIT_SQLITE=3 --
expect_rc "C3 a source that does not exist yet is not a failure" 0
expect_has "C3 and the run says which file was absent" "$scratch/out" "/data/deck/recorder.sqlite"
expect_has "C3 and calls it absent" "$scratch/out" "absent"

fresh
rm "$repo/book.sexp"
run_backup --
expect_rc "C4 a missing book.sexp fails the run" 1
expect_eq "C4 and everything else was still copied" "$(listing)" \
	"deploy-$today.env desk-$today.db jobs-$today.sqlite recorder-$today.sqlite"

# --- D. retention of what backup.sh writes ---------------------------------------
# The journal's dailies are pruned by `ohcamel journal-backup` (ruling 11) and
# are not this script's business. Its own are the sqlite copies and the two
# configuration copies: the 14 newest of each, by the date in the name.
fresh
d=1
while [ "$d" -le 16 ]; do
	: >"$backups/$(printf 'recorder-2026-09-%02d.sqlite' "$d")"
	d=$((d + 1))
done
d=13
while [ "$d" -le 26 ]; do
	: >"$backups/$(printf 'book-2026-09-%02d.sexp' "$d")"
	d=$((d + 1))
done
: >"$backups/deploy-2026-09-25.env"
: >"$backups/deploy-2026-09-26.env"
# Leftovers of a killed run, and files that are not this script's to judge.
: >"$backups/recorder-2026-09-20.sqlite.part"
: >"$backups/recorder-2026-09-20.sqlite.part-journal"
: >"$backups/desk-2026-08-01.db"
: >"$backups/pre-deploy-0000000-20260901T000000Z.db"
: >"$backups/notes.txt"
: >"$backups/recorder-notes.sqlite"
: >"$backups/jobs-2026-09-01.sqlite"
run_backup --
expect_rc "D1 a run over a full directory" 0
# recorder: 16 planted (09-01..09-16) + today's = 17; 17 - 14 = 3 deleted, the
# three oldest: 09-01, 09-02, 09-03. Kept: 09-04..09-16 (13) + 09-27 = 14.
want=""
d=4
while [ "$d" -le 16 ]; do
	want="$want$(printf 'recorder-2026-09-%02d.sqlite' "$d") "
	d=$((d + 1))
done
want="${want}recorder-$today.sqlite"
got=$(cd "$backups" && find . -name 'recorder-2026-*.sqlite' | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//')
expect_eq "D2 17 recorder copies become the 14 newest (09-01, 09-02, 09-03 deleted)" "$got" "$want"
# book: 14 planted (09-13..09-26) + today's = 15; 15 - 14 = 1 deleted: 09-13.
want=""
d=14
while [ "$d" -le 26 ]; do
	want="$want$(printf 'book-2026-09-%02d.sexp' "$d") "
	d=$((d + 1))
done
want="${want}book-$today.sexp"
got=$(cd "$backups" && find . -name 'book-*.sexp' | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//')
expect_eq "D3 15 book copies become the 14 newest (09-13 deleted)" "$got" "$want"
# deploy env: 2 planted + today's = 3, under 14: nothing deleted.
got=$(cd "$backups" && find . -name 'deploy-*.env' | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//')
expect_eq "D4 3 deploy.env copies are under 14 and all stay" "$got" \
	"deploy-2026-09-25.env deploy-2026-09-26.env deploy-$today.env"
for gone in recorder-2026-09-20.sqlite.part recorder-2026-09-20.sqlite.part-journal; do
	if [ -e "$backups/$gone" ]; then
		no "D5 the leftover $gone is deleted" "still there"
	else
		ok "D5 the leftover $gone is deleted"
	fi
done
for kept in desk-2026-08-01.db pre-deploy-0000000-20260901T000000Z.db notes.txt \
	recorder-notes.sqlite jobs-2026-09-01.sqlite; do
	if [ -e "$backups/$kept" ]; then
		ok "D6 $kept is not this script's to delete, and stays"
	else
		no "D6 $kept is not this script's to delete, and stays" "deleted"
	fi
done

# A copy that failed prunes nothing of its own: 16 planted stay 16.
fresh
d=1
while [ "$d" -le 16 ]; do
	: >"$backups/$(printf 'recorder-2026-09-%02d.sqlite' "$d")"
	d=$((d + 1))
done
run_backup SHIM_EXIT_SQLITE=1 --
got=$(cd "$backups" && find . -name 'recorder-*.sqlite' | grep -c . || true)
expect_eq "D7 a failed recorder copy prunes no recorder copy (16 stay 16)" "$got" "16"

# --- E. --dry-run and --check ---------------------------------------------------------
fresh
run_backup -- --dry-run
expect_rc "E1 --dry-run" 0
expect_eq "E1 --dry-run runs no container" "$(runs)" "0"
expect_eq "E1 --dry-run writes nothing" "$(listing)" ""
# One printed command a list line, each beginning `+ ` and naming compose run.
expect_eq "E2 --dry-run prints one container run a list line" \
	"$(grep -c '^+ .*docker compose .* run ' "$scratch/out" || true)" "$n_entries"
expect_has "E2 --dry-run prints the journal's command" "$scratch/out" "journal-backup /data/desk.db /backups"
expect_has "E2 --dry-run prints the recorder's destination" "$scratch/out" "recorder-$today.sqlite"

fresh
run_backup -- --check
expect_rc "E3 --check on the committed list" 0
expect_eq "E3 --check makes no docker call at all" "$(calls)" "0"
expect_eq "E3 --check writes nothing" "$(listing)" ""
expect_has "E3 --check prints the entries it read" "$scratch/out" "/data/deck/recorder.sqlite"

fresh
run_backup -- --frobnicate
expect_rc "E4 an unknown argument is refused" 2
expect_eq "E4 and nothing ran" "$(calls)" "0"

# --- F. the backup directory ------------------------------------------------------------
# Docker creates a missing bind-mount source as a root-owned directory, which
# uid 10001 then cannot write: the first backup would fail and leave a
# directory that makes every later one fail too. Refused here instead.
fresh
rmdir "$backups"
run_backup --
expect_rc "F1 a missing backup directory is refused" 1
expect_eq "F1 before any container runs" "$(runs)" "0"
expect_has "F1 and the refusal names the directory" "$scratch/err" "$backups"
if [ -e "$backups" ]; then
	no "F1 and the directory was not created behind the owner's back" "it exists now"
else
	ok "F1 and the directory was not created behind the owner's back"
fi

finish
