"""Command line entry point: permita38 [serve|migrate|backup|import-json|create-migration|...]."""

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
    sub.add_parser("migrate", help="apply pending database migrations (a backup is taken first)")
    bp = sub.add_parser("backup", help="write a consistent copy of the database")
    bp.add_argument("--dest", type=Path, help="target file (default: <data>/backups/permita38-<timestamp>.sqlite3)")
    ip = sub.add_parser("import-json", help="import data from the old JSON-file layout")
    ip.add_argument("old_data_dir", type=Path)
    cp = sub.add_parser("create-migration", help="create the next numbered migration file (all dialects)")
    cp.add_argument("name")
    dp = sub.add_parser("dump-ddl", help="print the schema DDL generated for a SQL dialect")
    dp.add_argument("--dialect", default="sqlite", choices=["sqlite", "postgresql", "mariadb"])
    args = ap.parse_args(argv)

    command = args.command or "serve"
    if command == "create-migration":
        from permita38.core.database import create_migration

        for p in create_migration(args.name):
            print("created", p)
        print("Now write the SQL for every dialect and bump CURRENT_REVISION in permita38/core/database.py.")
        return
    if command == "dump-ddl":
        from permita38.core.repository.dialect import DIALECTS
        from permita38.core.repository.schema import TABLES

        print(DIALECTS[args.dialect].ddl(TABLES))
        return

    cfg = load_config(args.config, data_dir=args.data, host=args.host, port=args.port)
    from permita38.api.deps import startup_services
    from permita38.api.server import get_app
    from permita38.core.repository import DatabaseRevisionError

    app = get_app(cfg)
    services = app.state.services
    if command == "show-openapi":
        print(json.dumps(app.openapi(), indent=2, ensure_ascii=False))
        return
    try:
        if command == "migrate":
            old, new = services.store.migrate()
            print(f"Database is at revision {new}." if old == new else f"Migrated revision {old} -> {new}.")
            return
        if command == "backup":
            services.store.startup()
            print("Backup written to", services.store.backup(args.dest))
            return
        if command == "import-json":
            from permita38.core.repository.importer import import_json

            services.store.startup()
            report = import_json(services.store, args.old_data_dir)
            for coll, n in report.imported.items():
                print(f"  {coll}: {n} imported, {report.skipped.get(coll, 0)} skipped")
            print(f"settings imported: {report.settings}; attachments copied: {report.attachments}")
            for line in report.usernames_derived:
                print("  username derived:", line)
            if report.users_without_password:
                print(
                    "These users have no password yet and cannot log in: "
                    + ", ".join(report.users_without_password)
                    + "\nRun `permita38 set-password <username>` for each of them."
                )
            return
        if command == "set-password":
            pw = getpass.getpass("New password: ")
            if pw != getpass.getpass("Repeat: "):
                sys.exit("Passwords do not match")
            try:
                services.auth.set_password(args.username, pw)
            except ServiceError as e:
                sys.exit(e.message)
            print("Password changed.")
            return
        startup_services(services)  # fail early, with a readable message
    except DatabaseRevisionError as e:
        sys.exit(f"permitA38 cannot use this database:\n{e}")

    import uvicorn

    from permita38.core.repository.importer import is_legacy_layout

    if is_legacy_layout(cfg.data_dir):
        print(
            "Note: the data folder still contains data in the old JSON layout. "
            "Import it once with: permita38 import-json " + str(cfg.data_dir)
        )
    url = f"http://{'localhost' if cfg.host in ('127.0.0.1', '0.0.0.0') else cfg.host}:{cfg.port}/"
    print(f"permitA38 is running at {url}\nData folder: {cfg.data_dir}\nPress Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
