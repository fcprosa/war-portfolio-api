"""Import-path bootstrap for Gatto Farioli.

The package uses flat imports (``from storage.db import …``) so modules work
when the working directory is ``gatto_farioli/``. When invoked as
``python -m gatto_farioli.run`` from the repo root, Python puts the repo root
on ``sys.path`` instead — this helper prepends the package directory once.
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent


def ensure_import_paths() -> Path:
    """Ensure ``gatto_farioli/`` is on ``sys.path`` for flat imports."""
    pkg = str(PKG_ROOT)
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    return PKG_ROOT
