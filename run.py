"""PyInstaller entry wrapper.

PyInstaller treats the entry script as a top-level module, so ``__main__.py``
cannot use its package-relative imports. This shim imports the package first
(relative imports then resolve normally) and hands off to the same dispatcher
that ``python -m gsa_seo_indexer`` uses.
"""

import sys

from gsa_seo_indexer import __main__ as app

if __name__ == "__main__":
    sys.exit(app.main())
