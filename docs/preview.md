# Local preview

`scripts/preview.sh` starts permitA38 from this checkout with fake demo data, so you can try the state of the
integration branch `preview` (an unpushed merge of all open feature branches) without touching your real data.

```
scripts/preview.sh          # then open http://127.0.0.1:8765
```

* Login: user `demo`, password `demo`. **This is for the local preview only**: the password is shorter than the
  production policy (8 characters) allows. `scripts/seed_demo.py` sets it by writing the hash directly after the
  normal setup call; the production policy is not changed. The server is bound to `127.0.0.1`, do not expose it.
  Two more fake users exist: `erin` (employee) and `alex` (accountant), password `preview-password-<name>`.
* Data folder: `~/permitA38-preview` (`PREVIEW_DIR` overrides it). On the first run `scripts/seed_demo.py` fills it
  through the public API with invented customers, projects, six weeks of time entries, a finalized invoice, offers,
  expenses and absences. Reset by deleting the folder.
* Your real configuration is not read or written: the script sets `PERMITA38_DATA`, `PERMITA38_IN_CONTAINER=1`
  (no `config.yaml`) and `XDG_CONFIG_HOME` inside the preview folder. Port: `PERMITA38_PORT` (default 8765).
* Migrations: the chain is `0001-initial` .. `0003-planning` (legacy ids), then `0004-numbering`, `0005-calendars`,
  `0006-roles`. A data folder from `main` migrates automatically (a backup is taken first).
