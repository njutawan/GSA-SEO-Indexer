"""Start the GUI, or the CLI when arguments are given.

``python -m gsa_seo_indexer`` opens the window;
``python -m gsa_seo_indexer submit --url https://example.com/`` runs the
command-line front-end instead.  The dispatch matters: the GUI never looks
at argv, so routing *everything* to it made ``--help`` hang headless and
forced the CLI to be invoked as ``python -m gsa_seo_indexer.cli``.
"""

import sys


def main() -> None:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
