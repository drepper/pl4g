"""The PL4G bootstrap compiler.

This module is deliberately kept free of imports: ``python3 -m pypl4g`` must not
pay for loading the whole compiler before the command line has even been parsed.
"""

VERSION: str = "0.1"
