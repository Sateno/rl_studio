"""Shared test configuration.

The render scripts in scripts/ import their shared module ``render_verification`` as a sibling,
which works when a script is run as ``python scripts/NAME.py``. Tests load those scripts by path,
so the scripts folder is put on the import path here. (It is absent in mutmut's generated
worktree; the tests that need the scripts skip themselves there.)
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if SCRIPTS.is_dir() and str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
