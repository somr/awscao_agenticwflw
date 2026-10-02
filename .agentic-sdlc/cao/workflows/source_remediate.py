"""Local entry point; install a frozen standalone bundle with install_source_remediate.sh."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdlc_workflows.source_remediation import INPUTS, main

if __name__ == '__main__':
    main()
