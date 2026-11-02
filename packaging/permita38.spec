# PyInstaller spec for the standalone desktop bundle (onedir: fewer antivirus false positives than onefile).
# Build from the repository root:   pyinstaller packaging/permita38.spec --noconfirm --clean
# The bundle contains the interpreter, all dependencies, the web frontend and the SQL migrations.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is provided by PyInstaller)

datas = [
    (str(root / "web"), "web"),  # permita38.core.config.default_web_dir() looks for <package root>/web
    (str(root / "permita38" / "core" / "templates"), "permita38/core/templates"),
    (str(root / "permita38" / "core" / "trip_rates"), "permita38/core/trip_rates"),
    (str(root / "permita38" / "core" / "schema" / "migrations"), "permita38/core/schema/migrations"),
]

# pypdfium2 ships the pdfium library as a data file / binary of its own
datas += collect_data_files("pypdfium2_raw") + collect_data_files("pypdfium2")
binaries = collect_dynamic_libs("pypdfium2_raw")

# uvicorn/starlette import protocol, loop and lifespan implementations dynamically
hiddenimports = collect_submodules("uvicorn") + collect_submodules("permita38") + ["multipart", "yaml", "pypdfium2", "pypdfium2_raw", "zxingcpp"]

a = Analysis(  # noqa: F821
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "playwright", "hypothesis", "unittest.mock"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="permita38",
    console=True,  # keep the console: it shows the URL and the log, and Ctrl+C stops the server
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="permita38", upx=False)  # noqa: F821
