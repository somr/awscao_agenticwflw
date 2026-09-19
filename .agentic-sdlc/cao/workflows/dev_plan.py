"""Local entry point. Install a standalone bundle with install.sh.

CAO deployments must be built with ../build_workflow.py, not copied from here.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdlc_workflows.planning import INPUTS, main

if __name__ == "__main__":
    main()
