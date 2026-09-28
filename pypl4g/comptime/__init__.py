"""Working a program out at compile time rather than compiling it.

What asks for this is the build function: a program says how it is built by
writing a function the compiler *runs*, and what that function leaves behind is
what gets compiled.  Nothing of it reaches the image, so everything in it has to
be something the compiler can work out for itself -- which is what this does, and
what it refuses is reported rather than silently left to run time.
"""

from __future__ import annotations

from .evaluate import CannotEvaluate, Evaluator, Reference, Record

__all__ = ["CannotEvaluate", "Evaluator", "Record", "Reference"]
