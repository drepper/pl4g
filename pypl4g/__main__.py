"""Entry point of the bootstrap compiler: ``python3 -m pypl4g``."""

from __future__ import annotations

import sys

from .driver.main import main

sys.exit(main(sys.argv[1:]))
