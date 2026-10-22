"""Command line entry point: permita38 [serve|show-openapi|set-password]."""

import argparse
import getpass
import json
import logging
import logging.config
import sys
import threading
import webbrowser
from pathlib import Path

from permita38.core.bootstrap import bootstrap_from_env, create_user, read_password_file
from permita38.core.config import load_config
from permita38.core.errors import ServiceError


def build_log_config(level: str = "info") -> dict:
    """Logging to stdout (what `docker logs` shows) for the app and for uvicorn."""
    level = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "plain", "stream": "ext://sys.stdout"}},
        "loggers": {
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"handlers": ["stdout"], "level": level, "propagate": False},
            "permita38": {"handlers": ["stdout"], "level": level, "propagate": False},
        },
    }


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
    cu = sub.add_parser("create-user", help="create a user (e.g. via `docker compose run --rm permita38 create-user`)")
    cu.add_argument("--username", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", choices=("boss", "member"), default="member")
    cu.add_argument("--password-file", type=Path, help="read the password from this file (default: prompt)")
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

    if command == "create-user":
        try:
            user_pw: str | None = read_password_file(args.password_file) if args.password_file else None
        except OSError as e:
            sys.exit(f"Cannot read {args.password_file}: {e.strerror}")
        if user_pw is None:
            user_pw = getpass.getpass("Password: ")
            if user_pw != getpass.getpass("Repeat: "):
                sys.exit("Passwords do not match")
        try:
            user = create_user(app.state.services.auth, args.username, args.name, args.role, user_pw)
        except ServiceError as e:
            sys.exit(e.message)
        print(f"Created {user['role']} {user['username']!r}.")
        return

    import uvicorn

    log_level = (cfg.log_level or ("info" if cfg.in_container else "warning")).lower()
    logging.config.dictConfig(build_log_config(log_level))
    try:
        bootstrap_from_env(app.state.services.auth)
    except RuntimeError as e:
        sys.exit(str(e))
    if cfg.in_container and app.state.services.auth.needs_setup():
        logging.getLogger("permita38").warning(
            "No users yet: the first-run setup page is open to anyone who can reach this server. "
            "Set PERMITA38_BOOTSTRAP_USERNAME / PERMITA38_BOOTSTRAP_PASSWORD(_FILE) to create the boss account at startup."
        )

    url = f"http://{'localhost' if cfg.host in ('127.0.0.1', '0.0.0.0') else cfg.host}:{cfg.port}/"
    print(f"permitA38 is running at {url}\nData folder: {cfg.data_dir}\nPress Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_config=build_log_config(log_level),
        proxy_headers=cfg.trust_proxy,
        forwarded_allow_ips=cfg.forwarded_allow_ips if cfg.trust_proxy else None,
    )


if __name__ == "__main__":
    main()
