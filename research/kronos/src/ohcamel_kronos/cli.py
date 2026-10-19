from __future__ import annotations

from pathlib import Path

import click

from . import forecast

ROOT = Path(__file__).resolve().parents[4]          # research/kronos/src/ohcamel_kronos -> repository root
DEFAULT_CONFIG = ROOT / "research/experiments/EXP-K01/config.yaml"


@click.group()
def cli() -> None:
    """EXP-K01: Kronos sampled as a one-day variance forecaster."""


@cli.command("forecast")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=DEFAULT_CONFIG)
@click.option("--out", type=click.Path(path_type=Path), default=None,
              help="Default: forecasts.csv beside the config. A partial run (--symbols/--limit-days) is "
                   "marked complete: false in its sidecar and is refused by the scorer.")
@click.option("--symbols", default=None, help="Comma-separated subset (engineering runs only).")
@click.option("--limit-days", type=int, default=None, help="First N evaluation days per name (engineering only).")
def forecast_cmd(config: Path, out: Path | None, symbols: str | None, limit_days: int | None) -> None:
    """Write every name-day's Kronos variance forecast for EXP-K01 (CPU, ~1 h for the full run)."""
    path = forecast.run(config, ROOT, out, symbols.split(",") if symbols else None, limit_days,
                        progress=lambda m: click.echo(m, err=True))
    click.echo(str(path))
