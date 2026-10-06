"""``python -m ohcamel_quant.warehouse.constituents``: refresh the committed lists (networked, by hand)."""

from . import main

if __name__ == "__main__":  # pragma: no cover - a manual, networked refresh
    raise SystemExit(main())
