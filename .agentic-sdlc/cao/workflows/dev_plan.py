"""Local entry point for development. Install into CAO with install.sh.

The installer deploys the package as a frozen library with a generated entry script; do not copy this file.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdlc_workflows.planning import INPUTS, main

if __name__ == "__main__":
    main()
