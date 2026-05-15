"""Allow ``python -m gatto_farioli`` as an alias for ``python run.py``."""

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from run import main

if __name__ == "__main__":
    raise SystemExit(main())
