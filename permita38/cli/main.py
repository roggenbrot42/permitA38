"""Command line entry point: permita38 [serve|migrate|backup|import-json|create-migration|...]."""

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


def _import_documents(services, args) -> None:
    from permita38.core.errors import ServiceError

    services.store.startup()
    options = {
        "on_duplicate": args.on_duplicate,
        "accept_unverified": args.accept_unverified,
        "merge_similar": args.merge_similar,
        "decimal": args.decimal,
        "date_order": args.date_order,
    }
    kwargs = dict(
        mapping=args.mapping.read_text(encoding="utf-8") if args.mapping else None,
        options=options,
        pdfs=args.pdfs.read_bytes() if args.pdfs else None,
        sheet=args.sheet,
        delimiter=args.delimiter,
    )
    data = args.file.read_bytes()
    try:
        if args.dry_run:
            report = services.imports.plan(data, args.file.name, args.type, **kwargs)
        else:
            report = services.imports.run(data, args.file.name, args.type, **kwargs)
    except ServiceError as e:
        sys.exit(e.message)
    counts = report["counts"]
    print(
        f"{args.type}: {report['rows_total']} rows - create {counts['create']}, skip {counts['skip']}, "
        f"overwrite {counts['overwrite']}, errors {counts['error']}; customers created {counts['customers_created']}, "
        f"merged {counts['customers_merged']}"
    )
    for row in report["rows"]:
        for msg in row["messages"]:
            print(f"  row {row['row']}: {row['action']}: {msg}")
    if report.get("unmapped_headers"):
        print("  columns not used:", ", ".join(report["unmapped_headers"]))
    if args.dry_run:
        print("Dry run: nothing was written.")
        return
    if not report["applied"]:
        sys.exit("Nothing was imported: fix the error rows above (or the mapping) and run again.")
    print(f"Imported. Batch {report['batch_id']} (undo: POST /api/import/batches/{report['batch_id']}/undo).")
    if report.get("next_number") and report.get("next_number") != report.get("next_number_before"):
        print(f"  the next number is now {report['next_number']} (was {report['next_number_before']})")


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
    rp = sub.add_parser("reset-totp", help="turn off two-factor authentication of a user (lock-out recovery)")
    rp.add_argument("username")
    mp = sub.add_parser("migrate", help="apply pending database migrations (a backup is taken first)")
    mp.add_argument("--status", action="store_true", help="list the migration chain with an applied marker and exit")
    bp = sub.add_parser("backup", help="write a consistent copy of the database")
    bp.add_argument("--dest", type=Path, help="target file (default: <data>/backups/permita38-<timestamp>.sqlite3)")
    ip = sub.add_parser("import-json", help="import data from the old JSON-file layout")
    ip.add_argument("old_data_dir", type=Path)
    idp = sub.add_parser("import-documents", help="import invoices, offers, customers or expenses (CSV, XLSX, JSON)")
    idp.add_argument("file", type=Path)
    idp.add_argument("--type", required=True, choices=["invoices", "offers", "customers", "expenses"])
    idp.add_argument("--mapping", type=Path, help="JSON mapping file (default: detect the columns by header names)")
    idp.add_argument("--pdfs", type=Path, help="zip of original PDFs named by document number")
    idp.add_argument("--dry-run", action="store_true", help="validate and report only; write nothing")
    idp.add_argument("--on-duplicate", choices=["skip", "overwrite"], default=None)
    idp.add_argument("--merge-similar", action="store_true", help="merge customers whose names are >= 90% similar")
    idp.add_argument("--accept-unverified", action="store_true", help="import rows whose totals do not add up")
    idp.add_argument("--sheet", help="worksheet name for XLSX files (default: the first sheet)")
    idp.add_argument("--delimiter", help="CSV delimiter (default: detected)")
    idp.add_argument("--decimal", choices=["auto", "comma", "point"], default=None)
    idp.add_argument("--date-order", choices=["dmy", "mdy"], default=None)
    cp = sub.add_parser(
        "create-migration", help="create a new migration file (random id, requires = current head) in all dialects"
    )
    cp.add_argument("name")
    dp = sub.add_parser("dump-ddl", help="print the schema DDL generated for a SQL dialect")
    dp.add_argument("--dialect", default="sqlite", choices=["sqlite", "postgresql", "mariadb"])
    cal = sub.add_parser("calendar", help="calendar subscriptions")
    cal_sub = cal.add_subparsers(dest="calendar_command", required=True)
    cal_sub.add_parser("refresh", help="download every connected calendar link now (files are not touched)")
    cu = sub.add_parser("create-user", help="create a user (e.g. via `docker compose run --rm permita38 create-user`)")
    cu.add_argument("--username", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", choices=("boss", "employee", "accountant", "member"), default="employee")
    cu.add_argument("--password-file", type=Path, help="read the password from this file (default: prompt)")
    args = ap.parse_args(argv)

    command = args.command or "serve"
    if command == "create-migration":
        from permita38.core.database import create_migration

        for p in create_migration(args.name):
            print("created", p)
        print("'requires' is set for you; there is no revision constant to bump. Now write the SQL for every dialect.")
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
            if args.status:
                from permita38.core.database import list_migrations

                applied = set(services.store.applied_ids())
                for m in list_migrations(services.store.backend):
                    print(f"[{'x' if m.id in applied else ' '}] {m.id}  {m.file_name}")
                return
            old, new, n = services.store.migrate()
            print(
                f"Database is at revision {new}."
                if n == 0
                else f"Migrated {old or 'none'} -> {new} ({n} migrations applied)."
            )
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
        if command == "import-documents":
            return _import_documents(services, args)
        if command == "calendar":
            services.store.startup()
            for name, status in services.calendar.refresh_all():
                print(f"  {name}: {status}")
            return
        if command == "reset-totp":
            services.store.startup()
            try:
                services.totp.reset_by_username(args.username)
            except ServiceError as e:
                sys.exit(e.message)
            print(f"Two-factor authentication of {args.username!r} is off. They are signed out everywhere.")
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

    from permita38.core.repository.importer import is_legacy_layout

    if is_legacy_layout(cfg.data_dir):
        print(
            "Note: the data folder still contains data in the old JSON layout. "
            "Import it once with: permita38 import-json " + str(cfg.data_dir)
        )
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
