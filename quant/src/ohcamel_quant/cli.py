"""``ohcamel-quant`` command line: serve the API, or warm the data cache."""

from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(prog="ohcamel-quant")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API + web app")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8090)
    sub.add_parser("warm", help="prefetch the datasets the landing pages use")
    a = p.parse_args()
    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("ohcamel_quant.api.app:app", host=a.host, port=a.port, proxy_headers=True)
    elif a.cmd == "warm":
        from .data.warm import warm

        warm()


if __name__ == "__main__":
    main()
