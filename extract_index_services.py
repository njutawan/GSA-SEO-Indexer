"""Extract the paid index-service definitions into readable files.

Dumps every service from ``gsa_seo_indexer/index_service/*.ini`` into
``index_services_extract.json`` (full structured data) and
``index_services_extract.md`` (a readable table).
"""

import json

from gsa_seo_indexer.services import load_services

svcs = load_services("gsa_seo_indexer/index_service")
print("terbaca:", len(svcs), "layanan")

out = []
for s in svcs:
    out.append(
        {
            "name": s.name,
            "enabled": s.enabled,
            "main_url": s.main_url,
            "desc": s.desc,
            "rating": s.rating,
            "costs": s.costs,
            "endpoint": s.url,
            "encoding": s.encoding,
            "header": s.header,
            "url_devider": s.url_devider,
            "ok_reply": s.ok_reply,
            "min_amount": s.min_amount,
            "max_amount": s.max_amount,
            "post_data_template": s.post_data,
            "variables": {
                k: {
                    "type": v.get("type", ""),
                    "default": v.get("default", ""),
                    "hint": v.get("hint", ""),
                    "list": v.get("list", ""),
                }
                for k, v in s.variables.items()
                if k != "error_message"
            },
        }
    )

with open("index_services_extract.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
print("ditulis: index_services_extract.json")

lines = [
    "# Daftar API index-service berbayar (diekstrak dari index_service/*.ini)",
    "",
    f"Total: **{len(out)} layanan** ({sum(1 for s in out if s['enabled'])} aktif bawaan, "
    f"{sum(1 for s in out if not s['enabled'])} nonaktif bawaan).",
    "",
    "| # | Layanan | Endpoint API | Biaya | Aktif | Min/Max URL | Variabel input |",
    "|---|---|---|---|---|---|---|",
]
for i, s in enumerate(out, 1):
    mm = f"{s['min_amount']}/{s['max_amount']}" if s["max_amount"] else "-/-"
    vars_ = ", ".join(s["variables"]) or "-"
    lines.append(
        f"| {i} | {s['name']} | `{s['endpoint']}` | {s['costs'] or '?'} | "
        f"{'ya' if s['enabled'] else 'tidak'} | {mm} | {vars_} |"
    )
lines += [
    "",
    "## Template POST tiap layanan",
    "",
]
for s in out:
    lines.append(f"### {s['name']}")
    lines.append(f"- endpoint: `{s['endpoint']}`")
    if s["post_data_template"]:
        lines.append(f"- post_data: `{s['post_data_template']}`")
    if s["url_devider"]:
        lines.append(f"- pemisah URL: `{s['url_devider']}`")
    if s["ok_reply"]:
        lines.append(f"- jawaban dianggap sukses: `{s['ok_reply']}`")
    lines.append("")

with open("index_services_extract.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("ditulis: index_services_extract.md")
print("contoh baris tabel:")
for l in lines[6:9]:
    print(" ", l)
