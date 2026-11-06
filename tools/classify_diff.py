#!/usr/bin/env python3
"""Equivalence harness for ``_classify_link``: full-body scan vs windowed scan.

The classifier decides whether a page merely *echoed* the submitted URL (in a
query string or a quoted attribute) or actually listed it.  The shipped
implementation removes every echo from the whole body with one regex and then
searches the remainder - correct, but ~1 ms on a 200 kB page.  The faster
variant walks back from each occurrence instead, which only reads a few dozen
characters per match; the risk is that the two disagree on an edge case, and a
disagreement that says "listed" where the old code said "" silently disables
the repair pass that finds missing backlinks.

This tool therefore keeps the old implementation verbatim as a reference and
compares the two over a generated corpus (plus a hand-written list of the
cases that matter).  Zero mismatches is the acceptance criterion.

Usage::

    python tools/classify_diff.py                 # 5000 generated cases
    python tools/classify_diff.py --fuzz 200000   # heavier run
    python tools/classify_diff.py --only-timing   # skip the comparison
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gsa_seo_indexer.engine import _QUERY_ECHO_RE, _classify_link  # noqa: E402

# ---------------------------------------------------------------------------
# The reference: the implementation as it shipped before the windowed scan.
# ---------------------------------------------------------------------------
_ANCHOR_RE = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', re.I)


def reference_classify(body: str, payload: str) -> str:
    """``_classify_link`` as it was: echo removal over the whole body."""
    if not body or not payload:
        return ""
    needle = payload.rstrip("/")
    bare = needle.split("://", 1)[-1].rstrip("/")
    if not bare or "." not in bare:
        return ""
    host = bare.split("/", 1)[0].lower()

    lowered = body.lower()
    if bare.lower() not in lowered and payload.lower() not in lowered:
        return ""

    for match in _ANCHOR_RE.finditer(body):
        href = match.group(1).strip()
        h = href.lower()
        if not h or h.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if h.startswith("/") or h.startswith("?") or h.startswith("."):
            continue
        if "://" not in h:
            continue
        after = h.split("://", 1)[1]
        link_host = after.split("/", 1)[0].split("?", 1)[0]
        if link_host != host:
            continue
        link_path = after.split("/", 1)[1] if "/" in after else ""
        target_path = bare.split("/", 1)[1] if "/" in bare else ""
        if link_path.split("?", 1)[0].strip("/") != target_path.split("?", 1)[0].strip("/"):
            continue
        return "indexed"

    stripped = _QUERY_ECHO_RE.sub(" ", body)
    if bare.lower() in stripped.lower() or payload.lower() in stripped.lower():
        return "listed"
    return ""


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
PAYLOADS = [
    "http://example.com/blog/post",
    "https://a.example/",
    "http://example.com",
    "http://example.com/x?a=1",
    "https://sub.example.co.uk/deep/path/page.html",
]

FILLERS = [
    "lorem ipsum dolor sit amet ",
    "<p>hasil pencarian</p>",
    "<div class='row'>",
    "&nbsp;",
    "\n",
]

# Every shape that has ever decided a classification.
TRICKY = [
    # echo in a query string only -> ""
    ("<form action='/search'><a href='/?url=http://example.com/blog/post'>cari</a></form>",
     "http://example.com/blog/post"),
    # echo plus a real mention -> "listed"
    ("<a href='/?url=http://example.com/blog/post'>cari</a> hasil: http://example.com/blog/post",
     "http://example.com/blog/post"),
    # quoted attribute echo only -> ""
    ('<meta property="og:url" content="http://example.com/blog/post">',
     "http://example.com/blog/post"),
    # quoted attribute plus visible text -> "listed"
    ('<meta content="http://example.com/blog/post"><p>http://example.com/blog/post</p>',
     "http://example.com/blog/post"),
    # real anchor -> "indexed"
    ('<a href="http://example.com/blog/post">post</a>', "http://example.com/blog/post"),
    # anchor to another host, mention in text -> "listed"
    ('<a href="http://other.example/">x</a> http://example.com/blog/post',
     "http://example.com/blog/post"),
    # bare host with trailing slash differences
    ("<p>example.com</p>", "http://example.com/"),
    # query with the target as a parameter value, unencoded
    ("<a href='/?q=http://example.com/blog/post&page=2'>2</a>", "http://example.com/blog/post"),
    # the target appears inside a JS string
    ("<script>var u='http://example.com/blog/post';</script>", "http://example.com/blog/post"),
    # whitespace inside the query run terminates the echo
    ("<a href='/?q=http://example.com/blog/post'>x</a><p>http://example.com/blog/post</p>",
     "http://example.com/blog/post"),
    # payload with a query string, echoed plus listed
    ("<a href='/?u=http://example.com/x?a=1'>x</a> http://example.com/x?a=1",
     "http://example.com/x?a=1"),
    # no mention at all
    ("<html><body>nothing here</body></html>", "http://example.com/blog/post"),
    # '=' followed by a query and only then a quote: removing the query first
    # would *create* a match for the second alternative, which the single pass
    # never sees.  A naive "sub twice" split gets this one wrong.
    ('<a href= ?url="http://example.com/blog/post">x</a>', "http://example.com/blog/post"),
    ('<a href=\t?u="http://example.com/blog/post">x</a>', "http://example.com/blog/post"),
    # query run immediately followed by a quoted URL
    ('?q="http://example.com/blog/post"', "http://example.com/blog/post"),
    ('x?y="http://example.com/blog/post"', "http://example.com/blog/post"),
    # unclosed quote: the second alternative needs a closing quote
    ('content="http://example.com/blog/post', "http://example.com/blog/post"),
    # upper-case scheme is not matched by the case-sensitive regex
    ('content="HTTP://example.com/blog/post"', "http://example.com/blog/post"),
    # mixed quotes still close the value
    ("content=\"http://example.com/blog/post'", "http://example.com/blog/post"),
    # long run before the occurrence (backscan budget)
    ("a" * 200_000 + "http://example.com/blog/post", "http://example.com/blog/post"),
    # occurrence right at the start of the body
    ("http://example.com/blog/post then text", "http://example.com/blog/post"),
    # same needle repeated inside echoes many times
    ("".join(f'?u{i}=http://example.com/blog/post ' for i in range(100)),
     "http://example.com/blog/post"),
]


def generate(rng: random.Random) -> tuple[str, str]:
    payload = rng.choice(PAYLOADS)
    bare = payload.split("://", 1)[-1]
    encoded = payload.replace(":", "%3A").replace("/", "%2F")
    pieces = [
        f'<a href="{payload}">link</a>',
        f"<a href='http://other.example/{bare}'>other</a>",
        f'<a href="/{bare}">relative</a>',
        f"?url={payload}",
        f"?q={payload}&page=2",
        f'="{payload}"',
        f"='{payload}'",
        f'<meta content="{payload}">',
        f"<p>{payload}</p>",
        f"<span>{bare}</span>",
        f"?u={encoded}",
        f"{payload}?from=1",
        f"x{payload}y",
        f"<script>var u = '{payload}';</script>",
        "?aaaaaaaaaa",
        "=\"https://other.example/?x=1\"",
        bare.rstrip("/"),
        # adversarial shapes: unclosed quotes, mixed quotes, odd spacing,
        # query strings that appear before an attribute, long runs
        f'="{payload}',
        f"'{payload}",
        f'?q="{payload}"',
        f'x?y="{payload}"',
        f"content = \"{payload}\"",
        f"content=\t\"{payload}\"",
        f"='{payload}\"",
        f'="{payload.upper()}"',
        f"?url={payload.upper()}",
        "\u00a0?url=" + payload,
        "\t?q=" + payload,
        f">{payload}<",
        f"?{payload}",
        f"?{payload}{payload}",
        "a" * 3000 + payload,
        payload + "?" + payload,
    ]
    body = "".join(
        rng.choice(FILLERS) + rng.choice(pieces) for _ in range(rng.randint(1, 12))
    )
    return body, payload


def windowed_classify(body: str, payload: str) -> str:
    """What the module currently does (imported, not re-implemented)."""
    return _classify_link(body, payload)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
def _big_body(kind: str) -> tuple[str, str]:
    payload = "http://example.com/blog/post"
    filler = "x " * 100_000
    if kind == "listed":
        return f"<html><body>{filler}{payload}</body></html>", payload
    if kind == "echo":
        return f"<html><body>{filler}?url={payload}</body></html>", payload
    return f"<html><body>{filler}</body></html>", payload


def timing(rounds: int = 100) -> None:
    print(f"\n{'kasus (200 kB)':22} {'lama':>10} {'sekarang':>10} {'perubahan':>12}")
    for kind in ("listed", "echo", "miss"):
        body, payload = _big_body(kind)
        start = time.perf_counter()
        for _ in range(rounds):
            reference_classify(body, payload)
        old = (time.perf_counter() - start) / rounds * 1000
        start = time.perf_counter()
        for _ in range(rounds):
            _classify_link(body, payload)
        new = (time.perf_counter() - start) / rounds * 1000
        delta = "lebih lambat" if new > old else f"{(1 - new / old) * 100:.0f}% lebih cepat"
        print(f"{kind:22} {old:8.3f} ms {new:8.3f} ms {delta:>12}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fuzz", type=int, default=5000, help="jumlah kasus acak")
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--show", type=int, default=5, help="cetak maksimum N selisih")
    parser.add_argument("--only-timing", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.only_timing:
        timing()
        return 0

    rng = random.Random(args.seed)
    cases = list(TRICKY) + [generate(rng) for _ in range(args.fuzz)]

    # The fast path never touches the echo regex; counting ``sub`` calls tells
    # us how many cases actually exercised it instead of the fallback.
    calls = [0]

    class _Counting:
        def sub(self, *a, **k):
            calls[0] += 1
            return _QUERY_ECHO_RE.sub(*a, **k)

    mismatches: list[tuple[str, str, str, str]] = []
    engine = sys.modules["gsa_seo_indexer.engine"]
    original = engine._QUERY_ECHO_RE
    engine._QUERY_ECHO_RE = _Counting()
    try:
        for body, payload in cases:
            expected = reference_classify(body, payload)
            actual = _classify_link(body, payload)
            if expected != actual:
                mismatches.append((body, payload, expected, actual))
    finally:
        engine._QUERY_ECHO_RE = original

    if not args.quiet:
        print(f"{len(cases)} kasus ({len(TRICKY)} tetap + {args.fuzz} acak, seed {args.seed})")
        print(f"  jalur cepat: {len(cases) - calls[0]} kasus, fallback regex: {calls[0]} kasus")
        for body, payload, expected, actual in mismatches[: args.show]:
            print(f"\n  payload  : {payload}")
            print(f"  harapan  : {expected!r}   sekarang: {actual!r}")
            print(f"  body     : {body[:300]!r}")
        if mismatches:
            print(f"\n{len(mismatches)} SELISIH")
        else:
            print("0 selisih - kedua implementasi sepakat di semua kasus")
        timing()
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
