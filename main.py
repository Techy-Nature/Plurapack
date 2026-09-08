"""Run Plurapack directly from a source checkout.

This module is a small, cross-platform alternative to the ``plurapack``
console script installed by the package.
"""

from plurapack.bot import main


if __name__ == "__main__":
    main()
