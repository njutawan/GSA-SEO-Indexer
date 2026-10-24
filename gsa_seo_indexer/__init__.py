"""GSA SEO Indexer clone — a re-implementation of the indexing mechanism used
by GSA SEO Indexer v3.09.

The original program pushes a target URL into thousands of statistics, whois
and network-log sites.  Those sites are crawled constantly by search-engine
spiders, so the target ends up exposed as a link on an authority domain, which
is what makes the crawler find it.

This package reimplements that mechanism from scratch: no GSA code, no
GSA binaries and no GSA resources are used.
"""

from .engine import (  # noqa: F401
    APP_NAME,
    APP_VERSION,
    Indexer,
    PingTarget,
    Result,
    RunStats,
    Settings,
    default_targets,
    load_ping_sites,
    read_url_file,
    save_ping_sites,
)

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "Indexer",
    "PingTarget",
    "Result",
    "RunStats",
    "Settings",
    "default_targets",
    "load_ping_sites",
    "read_url_file",
    "save_ping_sites",
]
