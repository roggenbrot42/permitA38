"""PyInstaller entry point of the standalone desktop bundle (same as `python -m permita38`)."""

import multiprocessing

from permita38.cli.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
