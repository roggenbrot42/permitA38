"""Command line entry point: permita38 [serve|show-openapi|set-password]."""

import argparse
import getpass
import json
import sys
import threading
import webbrowser
from pathlib import Path

from permita38.core.config import load_config
from permita38.core.errors import ServiceError


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(
        prog="permita38",
        description="permitA38 – time tracking, planning, offers, invoices and taxes for freelancers.",
    )
    ap.add_argument("-c", "--config", type=Path, help="config file (default: per-device user config dir)")
    ap.add_argument("--data", type=Path, help="data folder (inside your Syncthing share); remembered per device")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the web app and API (default)")
    sub.add_parser("show-openapi", help="print the OpenAPI contract as JSON")
    sp = sub.add_parser("set-password", help="reset the password of a user")
    sp.add_argument("username")
    args = ap.parse_args(argv)

    cfg = load_config(args.config, data_dir=args.data, host=args.host, port=args.port)
    from permita38.api.server import get_app

    command = args.command or "serve"
    app = get_app(cfg)
    if command == "show-openapi":
        print(json.dumps(app.openapi(), indent=2, ensure_ascii=False))
        return
    if command == "set-password":
        pw = getpass.getpass("New password: ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("Passwords do not match")
        try:
            app.state.services.auth.set_password(args.username, pw)
        except ServiceError as e:
            sys.exit(e.message)
        print("Password changed.")
        return

    import uvicorn

    url = f"http://{'localhost' if cfg.host in ('127.0.0.1', '0.0.0.0') else cfg.host}:{cfg.port}/"
    print(f"permitA38 is running at {url}\nData folder: {cfg.data_dir}\nPress Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
