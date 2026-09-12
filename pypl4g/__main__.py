"""Entry point of the bootstrap compiler: ``python3 -m pypl4g``."""

import sys

from .driver.main import main

sys.exit(main(sys.argv[1:]))
