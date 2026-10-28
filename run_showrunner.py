"""Entry point for running from source and for PyInstaller."""
import sys

from showrunner.app import main

if __name__ == "__main__":
    sys.exit(main())
