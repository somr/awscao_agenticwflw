"""Local entry point. Install a standalone bundle with install_deliver.sh.

CAO deployments must be built with ../build_workflow.py, not copied from here.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdlc_workflows.delivery import INPUTS, main

if __name__ == "__main__":
    main()
