"""Support ``python -m skillkernel``.

A second surface onto the same adapter. It resolves through the module system
rather than an installed console script, which makes it useful as an
independent check that the package itself is importable and complete.
"""

from __future__ import annotations

import sys

from skillkernel.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
