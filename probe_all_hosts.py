"""Probe TCP liveness of every host in the legacy + modern lists.

Answers "are all 6,671 legacy + 18 modern targets active?" with a
measurement, and fills the persistent dead-host cache as a side effect.
"""

import time

from gsa_seo_indexer.engine import (
    Indexer,
    Settings,
    default_targets,
    host_of,
    load_modern_targets,
    load_ping_sites,
)

s = Settings.load("gsa_seo_indexer/config.ini")
s.preflight = True
ix = Indexer(s)

print("memuat list...", flush=True)
sites = load_ping_sites()
targets = default_targets(sites)  # legacy: 2 forms per site
hosts_legacy = sorted({host_of(t.url) for t in targets if host_of(t.url)})
mt = load_modern_targets()
hosts_modern = sorted({host_of(t.url) for t in mt if host_of(t.url)})
print(f"host unik legacy: {len(hosts_legacy)}")
print(f"host modern     : {len(hosts_modern)}")
print("memulai probe TCP (connect timeout 4s)...", flush=True)

t0 = time.time()
alive = ix.preflight(default_targets(sites) + mt)
print(f"probe selesai dalam {time.time() - t0:.1f}s", flush=True)


def report(name, hosts):
    n_alive = sum(1 for h in hosts if h in alive)
    pct = n_alive / len(hosts) * 100 if hosts else 0.0
    print(f"{name}: {n_alive} alive / {len(hosts)} total ({pct:.1f}%)")


report("LEGACY", hosts_legacy)
report("MODERN", hosts_modern)
ix.host_cache.save(ix._host_cache_path())
print("host cache:", ix.host_cache.stats())
