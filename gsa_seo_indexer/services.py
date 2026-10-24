"""Indexing service support.

The original GSA SEO Indexer can hand a batch of URLs to paid third-party
"indexing services" described by INI files in an ``index_service`` folder.
This module reads those same INI files, so a service definition written for
GSA works here unchanged.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Where the user's paid-service credentials live: {service: {variable: value}}.
# It is a separate file rather than a config.ini section so the GSA-style
# config stays shareable and no key is written into a settings file that
# the GUI round-trips on every save.
KEYS_PATH = Path(__file__).resolve().parent / "keys.json"


@dataclass
class IndexService:
    """One indexing service definition."""

    name: str
    path: Path
    enabled: bool = True
    main_url: str = ""
    desc: str = ""
    rating: str = ""
    costs: str = ""
    url: str = ""
    post_data: str = ""
    encoding: str = ""
    header: str = ""
    url_devider: str = ""
    ok_reply: str = ""
    simulate_result: str = ""
    simulate_error: str = ""
    variables: dict[str, dict[str, str]] = field(default_factory=dict)
    min_amount: int = 0
    max_amount: int = 0

    # -- helpers ---------------------------------------------------------
    def _substitute(self, template: str, args: dict[str, str]) -> str:
        """Expand ``%arg1%`` / ``%API-Key%`` placeholders.

        Placeholder names are matched case-insensitively: the service INI
        files spell the same variable differently in different places (for
        example ``%index_in_google%`` in the body and ``[Index_in_Google]``
        as the section name).
        """
        out = template
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            pattern = re.compile(r"%(" + re.escape(key) + r")%", re.IGNORECASE)
            out = pattern.sub(lambda _m: value, out)
        return out

    def _merged_args(self, args: dict[str, str] | None) -> dict[str, str]:
        """Caller arguments win over the defaults declared in the INI."""
        merged: dict[str, str] = {}
        for name, items in self.variables.items():
            default = items.get("default")
            if default is not None:
                merged[name] = default
        merged.update(args or {})
        return merged

    def build_request(self, urls: list[str], args: dict[str, str] | None = None) -> tuple[str, str, str | None]:
        """Return ``(url, body, content_type)`` for a submission."""
        args = self._merged_args(args)
        args.setdefault("arg1", "\n".join(urls))
        args.setdefault("arg2", "")

        url = self._substitute(self.url, args)
        body = self._substitute(self.post_data, args) if self.post_data else None
        if self.url_devider and body and "%arg1%" not in self.post_data:
            body = self.url_devider.join(urls)
        content_type = None
        if body is not None:
            if self.encoding == "urlencode":
                content_type = "application/x-www-form-urlencoded"
            else:
                content_type = "application/json"
        return url, body, content_type

    def build_headers(self, args: dict[str, str] | None = None) -> dict[str, str]:
        args = self._merged_args(args)
        headers: dict[str, str] = {}
        for line in (self.header or "").split(";"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[self._substitute(key.strip(), args)] = self._substitute(value.strip(), args)
        return headers

    def prompt_variables(self) -> dict[str, str]:
        """Describe the variables the user still has to supply.

        The INI describes each variable with a ``type`` and a ``hint``; the
        ``default`` entry (when present) supplies the value used when the
        caller does not override it.
        """
        out: dict[str, str] = {}
        for name, items in self.variables.items():
            default = items.get("default")
            if default is not None:
                out[name] = default
        return out

    def is_success(self, body: str) -> bool:
        """Does the reply match the service's expected success marker?

        The INI stores one or more expected markers separated by ``|``, e.g.
        ``ok reply="success": true|"success":true``.  Any of them matching
        means success.
        """
        if not self.ok_reply:
            return True
        low = body.lower()
        for marker in self.ok_reply.split("|"):
            marker = marker.strip().lower()
            if marker and marker in low:
                return True
        return False

    def submit(self, urls: list[str], args: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[bool, str]:
        """Submit URLs.  Returns ``(ok, message)``."""
        url, body, content_type = self.build_request(urls, args)
        headers = self.build_headers(args)
        headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read(200000).decode("utf-8", "replace")
                if self.is_success(text):
                    return True, f"HTTP {resp.status}"
                return False, f"HTTP {resp.status}: unexpected reply"
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:160]


def load_services(folder: str | os.PathLike) -> list[IndexService]:
    """Load every indexing service definition from ``folder``."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    services: list[IndexService] = []
    for ini in sorted(folder.glob("*.ini")):
        # allow_no_value: GSA's own files are not always clean INI.
        # ``Lindexed.ini`` carries a bare documentation line between
        # sections that a strict parse chokes on, which used to drop that
        # whole service (48 files, only 47 were ever visible).
        cfg = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True)
        try:
            cfg.read_string(ini.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        svc = IndexService(name=ini.stem, path=ini)
        if cfg.has_section("setup"):
            svc.enabled = cfg.getboolean("setup", "enabled", fallback=True)
            svc.main_url = cfg.get("setup", "main url", fallback="")
            svc.desc = cfg.get("setup", "desc", fallback="")
            svc.rating = cfg.get("setup", "rating", fallback="")
            svc.costs = cfg.get("setup", "costs", fallback="")
        if cfg.has_section("index"):
            svc.url = cfg.get("index", "url", fallback="")
            svc.post_data = cfg.get("index", "post_data", fallback="")
            svc.encoding = cfg.get("index", "encoding", fallback="")
            svc.header = cfg.get("index", "header", fallback="")
            svc.url_devider = cfg.get("index", "url_devider", fallback="")
            svc.ok_reply = cfg.get("index", "ok reply", fallback="")
            svc.simulate_result = cfg.get("index", "simulate_result", fallback="")
            svc.simulate_error = cfg.get("index", "simulate_error", fallback="")
            # batch limits: e.g. Lindexed rejects calls under 10 URLs,
            # several services cap a single call at 10.000.
            svc.min_amount = cfg.getint("index", "min amount", fallback=0)
            svc.max_amount = cfg.getint("index", "max amount", fallback=0)
        for section in cfg.sections():
            # "error_message" and "balance" describe the protocol, not
            # values the user supplies - they must not masquerade as
            # input variables
            if section in ("setup", "index", "error_message", "balance"):
                continue
            svc.variables[section] = dict(cfg.items(section))
        services.append(svc)
    return services


def service_arguments(service: IndexService) -> dict[str, str]:
    """Turn the INI variable sections into a flat argument map.

    A section that declares ``default=`` supplies that value for the
    placeholder of the same name; sections without a default are left for
    the caller to fill in.
    """
    args: dict[str, str] = {}
    for section, items in service.variables.items():
        default = items.get("default")
        if default is not None:
            args.setdefault(section, default)
    return args


def load_keys(path: str | os.PathLike = KEYS_PATH) -> dict[str, dict[str, str]]:
    """Read the stored credentials as ``{service: {variable: value}}``.

    A missing or malformed file yields an empty store rather than an
    error: the paid services stay usable through their INI defaults.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    keys: dict[str, dict[str, str]] = {}
    for name, values in data.items():
        if isinstance(values, dict):
            keys[str(name)] = {
                str(k): str(v) for k, v in values.items() if isinstance(v, (str, int, float))
            }
    return keys


def save_keys(keys: dict[str, dict[str, str]], path: str | os.PathLike = KEYS_PATH) -> None:
    """Write the credential store atomically."""
    tmp = f"{path}.tmp"
    Path(tmp).write_text(
        json.dumps(keys, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, str(path))


def set_key(
    service: str,
    variable: str,
    value: str,
    path: str | os.PathLike = KEYS_PATH,
) -> None:
    """Store one credential for one service."""
    keys = load_keys(path)
    keys.setdefault(service, {})[variable] = value
    save_keys(keys, path)


def service_arguments_with_keys(
    service: IndexService,
    keys: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    """INI defaults, overridden by whatever the user has stored.

    Without this layering every paid submission goes out with the literal
    placeholder ``%API-Key%`` in the POST body, which no live service
    accepts; the only thing that used to make this path work at all was
    the emulator.
    """
    args = service_arguments(service)
    keymap = (keys if keys is not None else load_keys()).get(service.name, {})
    args.update({k: v for k, v in keymap.items() if v})
    return args


def set_service_enabled(path: str | os.PathLike, enabled: bool) -> None:
    """Flip ``enabled=`` in the service INI without touching anything else.

    A full ``configparser`` rewrite would drop every comment in the file,
    and those comments document the format, so the edit is done on the
    single ``enabled`` line instead - exactly how the original program
    edits the file too.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    value = "1" if enabled else "0"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.split("=", 1)[0].strip().lower() == "enabled":
            eol = "\n" if line.endswith("\n") else ""
            lines[i] = f"enabled={value}{eol}"
            break
    path.write_text("".join(lines), encoding="utf-8")
