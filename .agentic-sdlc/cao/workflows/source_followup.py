"""Local entry point for development; install into CAO (frozen library + entry script) with install_source_followup.sh."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdlc_workflows.source_followup import INPUTS, main

if __name__ == '__main__':
    main()
