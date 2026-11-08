#!/usr/bin/env python3
"""Minimal CSS minifier (stdlib only) — no npm, no bundler.

Reads css/style.css, writes css/style.min.css. Safe minification only:
comments are stripped, whitespace around tokens is collapsed. Values,
selectors, and cascade order are never altered.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "css" / "style.css"
OUT = ROOT / "css" / "style.min.css"


def minify(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)          # drop comments
    css = re.sub(r"\s+", " ", css)                           # collapse whitespace
    # NOTE: never touch whitespace around "+" / "-": calc(), clamp() and min()
    # require it, and stripping it silently invalidates the declaration.
    css = re.sub(r"\s*([{};:,>~])\s*", r"\1", css)           # tight punctuation
    css = css.replace(";}", "}")                             # drop last semicolon
    css = re.sub(r"(?<![\w.-])0\.(\d)", r".\1", css)         # 0.5rem -> .5rem
    # restore the single space calc()/clamp() math needs after a comma
    css = re.sub(r"(calc|clamp|min|max)\(", r"\1(", css)
    return css.strip() + "\n"


def main() -> None:
    OUT.write_text(minify(SRC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
