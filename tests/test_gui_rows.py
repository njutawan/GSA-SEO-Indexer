"""Tests for the GUI result-table cap (P1).

The GUI cannot be instantiated without a display, so these tests drive the two
pure-ish methods (``_insert_result_row`` / ``_update_shown_label``) on an
instance built with ``__new__`` and a fake tree.  When tkinter is not installed
at all (headless CI images often ship Python without it) a tiny stub is
installed first: the code paths under test never touch a real widget.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _Var:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _Widget:
    def __init__(self, *_args, **_kwargs):
        pass

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _install_tkinter_stub() -> None:
    if "tkinter" in sys.modules:
        return
    tk = types.ModuleType("tkinter")

    class Tk(_Widget):
        pass

    tk.Tk = Tk
    for name in ("StringVar", "BooleanVar", "IntVar", "DoubleVar"):
        setattr(tk, name, _Var)
    tk.TclError = Exception
    for name in ("END", "LEFT", "RIGHT", "TOP", "BOTTOM", "BOTH", "X", "Y", "NSEW"):
        setattr(tk, name, name.lower())

    ttk = types.ModuleType("tkinter.ttk")
    for name in (
        "Treeview", "Button", "Label", "Entry", "Combobox", "Progressbar",
        "Checkbutton", "Frame", "LabelFrame", "Scrollbar", "Style",
        "Separator", "Notebook",
    ):
        setattr(ttk, name, _Widget)
    filedialog = types.ModuleType("tkinter.filedialog")
    messagebox = types.ModuleType("tkinter.messagebox")

    tk.ttk, tk.filedialog, tk.messagebox = ttk, filedialog, messagebox
    sys.modules.update(
        {
            "tkinter": tk,
            "tkinter.ttk": ttk,
            "tkinter.filedialog": filedialog,
            "tkinter.messagebox": messagebox,
        }
    )


try:  # real tkinter (desktop installs)
    from gsa_seo_indexer import gui as gui_mod
except ImportError:  # headless image without tkinter
    _install_tkinter_stub()
    from gsa_seo_indexer import gui as gui_mod


class _FakeTree:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.detached: set[str] = set()

    def insert(self, _parent, _index, values=(), tags=()):
        iid = f"I{len(self.rows)}"
        self.rows.append(iid)
        return iid

    def get_children(self):
        return [iid for iid in self.rows if iid not in self.detached]

    def detach(self, iid):
        self.detached.add(iid)

    def set(self, _iid, column=None):
        return "" if column else {"target": "http://a/"}

    def tag_configure(self, *_args, **_kwargs):
        pass


def _bare_app():
    app = gui_mod.IndexerApp.__new__(gui_mod.IndexerApp)
    app.tree = _FakeTree()
    app._all_rows = []
    app._row_tag = {}
    app._hidden_rows = 0
    app.var_shown = _Var("0 rows")
    app.var_filter = _Var("All")
    app.var_search = _Var("")
    return app


class RowCapTest(unittest.TestCase):
    def test_rows_stop_at_the_cap_and_the_rest_are_counted(self) -> None:
        app = _bare_app()
        overflow = 7
        for i in range(gui_mod.MAX_TREE_ROWS + overflow):
            app._insert_result_row((f"http://t{i}/", "h", "indexed", 200, 1), "indexed")

        self.assertEqual(len(app._all_rows), gui_mod.MAX_TREE_ROWS)
        self.assertEqual(len(app.tree.rows), gui_mod.MAX_TREE_ROWS)
        self.assertEqual(app._hidden_rows, overflow)
        self.assertEqual(len(app._row_tag), gui_mod.MAX_TREE_ROWS)

        app._update_shown_label()
        label = app.var_shown.get()
        self.assertIn(f"{gui_mod.MAX_TREE_ROWS:,}", label)
        self.assertIn(str(gui_mod.MAX_TREE_ROWS + overflow), label)

    def test_below_the_cap_every_row_is_shown(self) -> None:
        app = _bare_app()
        for i in range(10):
            app._insert_result_row((f"http://t{i}/", "h", "indexed", 200, 1), "indexed")

        self.assertEqual(len(app.tree.rows), 10)
        self.assertEqual(app._hidden_rows, 0)
        app._update_shown_label()
        self.assertEqual(app.var_shown.get(), "10/10 rows")

    def test_the_engine_still_keeps_every_result(self) -> None:
        """The cap is display-only: the engine's result list is untouched."""
        app = _bare_app()
        results = [
            gui_mod.engine.Result(
                target=f"http://t{i}/", url="http://s/", status="submitted"
            )
            for i in range(gui_mod.MAX_TREE_ROWS + 3)
        ]
        for result in results:
            app._insert_result_row(
                (result.target, "h", "indexed", 200, 1), "indexed"
            )
        self.assertEqual(len(results), gui_mod.MAX_TREE_ROWS + 3)
        self.assertEqual(len(app._all_rows), gui_mod.MAX_TREE_ROWS)


if __name__ == "__main__":
    unittest.main()
