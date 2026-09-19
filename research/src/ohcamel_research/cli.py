"""The ohcamel-research command."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import click

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.data import (
    ProvenanceError,
    load_bars,
    read_provenance,
    slice_bars,
)
from ohcamel_research.contract import check as check_doc
from ohcamel_research.replay import DEFAULT_FIXTURES, to_jsonl

EXAMPLES = REPO_ROOT / "interface" / "examples"


def _date(_ctx: object, _param: object, value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@click.group()
def cli() -> None:
    """OhCamel research layer."""


@cli.command()
@click.option(
    "--fixtures", type=click.Path(path_type=Path), default=DEFAULT_FIXTURES, show_default=True
)
@click.option("--start", callback=_date, help="first bar date, inclusive")
@click.option("--end", callback=_date, help="last bar date, inclusive")
@click.option("--symbols", help="comma-separated subset")
def replay(fixtures: Path, start: date | None, end: date | None, symbols: str | None) -> None:
    """Stream committed REAL bars as JSON lines. Refuses files without provenance."""
    try:
        bars = load_bars(fixtures)
    except ProvenanceError as e:
        raise click.ClickException(str(e)) from e
    syms = [s.strip() for s in symbols.split(",")] if symbols else None
    sys.stdout.write(to_jsonl(slice_bars(bars, start, end, syms)))


@cli.group()
def fixtures() -> None:
    """The committed bar fixtures."""


@fixtures.command("doctor")
@click.option(
    "--fixtures", "fixtures_dir", type=click.Path(path_type=Path), default=DEFAULT_FIXTURES
)
def fixtures_doctor(fixtures_dir: Path) -> None:
    """Show every fixture's provenance, and refuse loudly if any lacks it."""
    bad = 0
    for p in sorted(fixtures_dir.glob("*.parquet")):
        try:
            doc = read_provenance(p)
            span = f"{doc['start']}..{doc['end']}"
            click.echo(f"  ok   {p.name:14} {doc['source']:8} {span}  synthetic={doc['synthetic']}")
        except ProvenanceError as e:
            bad += 1
            click.echo(f"  FAIL {e}")
    if bad:
        raise click.ClickException(f"{bad} fixture(s) without acceptable provenance")


@cli.group()
def signal() -> None:
    """Signals in the contract's shape."""


@signal.command("emit")
@click.option("--strategy", required=True)
@click.option("--params", default="{}", help="JSON object of strategy parameters")
@click.option("--as-of", "as_of", required=True, callback=_date)
@click.option("--sequence", type=int, required=True)
@click.option("--fixtures", type=click.Path(path_type=Path), default=DEFAULT_FIXTURES)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def signal_emit(
    strategy: str, params: str, as_of: date, sequence: int, fixtures: Path, out: Path
) -> None:
    """Run an fdq strategy point-in-time at AS_OF and write an UNVALIDATED signal.

    Unvalidated on purpose: the validation block is written by the battery in
    Phase 1, never by hand. The core will reject this under R6, which is the
    behaviour being demonstrated.
    """
    from ohcamel_research.signal import emit  # fdq import is slow; keep replay fast

    doc = emit(strategy, json.loads(params), as_of, load_bars(fixtures), sequence)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    status = doc["validation"]["status"]
    click.echo(f"wrote {out}: {strategy} as_of={as_of} targets={doc['targets']} status={status}")


@signal.command("check")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
def signal_check(path: Path) -> None:
    """Validate one signal document against the schema and R7."""
    problems = check_doc(json.loads(path.read_text()))
    if problems:
        for p in problems:
            click.echo(f"  {p}")
        raise click.ClickException(f"{path.name}: {len(problems)} problem(s)")
    click.echo(f"  ok   {path.name}")


@cli.group()
def battery() -> None:
    """The validation battery."""


@battery.command("run")
@click.argument("experiment_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
def battery_run(experiment_dir: Path) -> None:
    """Run the pre-registered experiment in EXPERIMENT_DIR and write one manifest per strategy.

    The experiment directory is the only input: the bars, the friction, the
    macro series, the windows and the seed all come from its config.yaml, and
    everything else from battery/, so every line the verdict depends on is
    hashed into the manifests. Refuses to start on an uncommitted battery.
    """
    from ohcamel_research.battery.config import ConfigError  # fdq import is slow
    from ohcamel_research.battery.run import describe_measure, run
    from ohcamel_research.manifest import manifest_path

    try:
        manifests = run(experiment_dir, write=True)
    except (ConfigError, ProvenanceError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    for m in manifests:
        click.echo(f"{m.slug}: {m.verdict_line}")
        click.echo(
            f"  turnover {describe_measure(m.turnover, '{:.2f} a year, one-way')}; "
            f"capacity {describe_measure(m.capacity, '${:,.0f}')}; "
            f"-> {manifest_path(experiment_dir, m.slug)}"
        )


@cli.group()
def contract() -> None:
    """The two-sided contract test."""


@contract.command("check")
@click.option("--core", type=click.Path(path_type=Path), help="path to the built OCaml core binary")
@click.option("--fixtures", type=click.Path(path_type=Path), default=DEFAULT_FIXTURES)
def contract_check(core: Path | None, fixtures: Path) -> None:
    """Run interface/examples through the schema, and through the core if given.

    expected.json states, for each example, whether the schema accepts it and
    what the core must say under the stated clock. Both sides are held to it.
    """
    expected = json.loads((EXAMPLES / "expected.json").read_text())
    clock = expected["clock"]
    failures = 0
    bars_file: Path | None = None
    if core is not None:
        if not core.exists():
            raise click.ClickException(f"core binary not found at {core}; build it first")
        dates = [date.fromisoformat(d) for d in clock["bar_dates"]]
        bars = slice_bars(load_bars(fixtures), min(dates), max(dates))
        seen = sorted({d.isoformat() for d in bars["date"]})
        if seen != sorted(clock["bar_dates"]):
            raise click.ClickException(
                f"fixture dates {seen} do not match expected clock {clock['bar_dates']}"
            )
        bars_file = EXAMPLES / ".clock.jsonl"
        bars_file.write_text(to_jsonl(bars))
    click.echo(f"{'example':26} {'schema':8} {'core':16}")
    for name, exp in expected["cases"].items():
        doc = json.loads((EXAMPLES / name).read_text())
        schema_ok = check_doc(doc) == []
        s_mark = "ok" if schema_ok == exp["schema_valid"] else "MISMATCH"
        c_mark = "-"
        if core is not None and bars_file is not None:
            args = [
                str(core),
                "intake",
                str(EXAMPLES / name),
                "--bars",
                str(bars_file),
                "--registered",
                ",".join(expected["registry"]["strategies"]),
                "--max-age",
                str(clock["max_age"]),
                "--universe",
                ",".join(expected["universe"]),
            ]
            last = expected["registry"]["last_sequence"]
            if last:
                args += ["--last-seq", ",".join(f"{k}={v}" for k, v in last.items())]
            r = subprocess.run(args, capture_output=True, text=True, check=False)
            first = (r.stdout.splitlines() or [""])[0]
            got = first.split(":")[0].strip() if first.startswith("REJECT") else first.split(" ")[0]
            c_mark = "ok" if got == exp["core"] else f"MISMATCH got {first[:40]!r}"
        if s_mark != "ok" or (c_mark not in ("ok", "-")):
            failures += 1
        click.echo(f"{name:26} {s_mark:8} {c_mark:16}")
    if failures:
        raise click.ClickException(f"{failures} contract mismatch(es)")
    click.echo("contract: both sides agree with expected.json")


if __name__ == "__main__":
    cli()
