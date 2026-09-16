"""The command-line adapter.

This package formats output and chooses exit codes. It holds no domain logic:
every decision it reports was made by the library. A test parses the AST of
these modules and fails if they reach past that boundary.
"""

from skillkernel.cli.main import main

__all__ = ["main"]
