"""Addon manifest validation and API compatibility rules (docs/manifest.md)."""
import json
import os
import re

from . import API_MAJOR, API_MINOR
from .permissions import is_known_permission

ID_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
API_RE = re.compile(r"^(\d+)\.(\d+)$")
SETTING_TYPES = {"boolean", "string", "number", "choice", "secret-env"}


class ManifestError(Exception):
    pass


def check_compat(api):
    """Return None when compatible, else a human readable reason."""
    m = API_RE.match(str(api))
    if not m:
        return f"invalid api version {api!r}"
    major, minor = int(m.group(1)), int(m.group(2))
    if major != API_MAJOR:
        return f"requires Gnotch API {api}, host provides {API_MAJOR}.{API_MINOR} (major mismatch)"
    if minor > API_MINOR:
        return f"requires Gnotch API {api}, host provides {API_MAJOR}.{API_MINOR} (host too old)"
    return None


def validate(m, addon_dir=None):
    """Return list of error strings (empty = valid)."""
    errs = []
    if not isinstance(m, dict):
        return ["manifest must be a JSON object"]

    def need(key, typ):
        if key not in m:
            errs.append(f"missing required field '{key}'")
            return False
        if not isinstance(m[key], typ):
            errs.append(f"field '{key}' must be {typ.__name__}")
            return False
        return True

    if need("id", str) and not ID_RE.match(m["id"]):
        errs.append("id must be reverse-DNS style, lowercase (e.g. com.example.clock)")
    need("name", str)
    if need("version", str) and not SEMVER_RE.match(m["version"]):
        errs.append("version must be semver (MAJOR.MINOR.PATCH)")
    need("api", str)
    perms = m.get("permissions", [])
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        errs.append("permissions must be a list of strings")
    else:
        for p in perms:
            if not is_known_permission(p):
                errs.append(f"unknown permission '{p}'")
    entry = m.get("entry")
    if entry is not None:
        if not isinstance(entry, dict) or not isinstance(entry.get("backend"), list) \
                or not entry["backend"] or not all(isinstance(a, str) for a in entry["backend"]):
            errs.append("entry.backend must be a non-empty argv list of strings")
        elif addon_dir:
            first = entry["backend"][-1] if entry["backend"][0] in ("python3", "node", "sh", "bash") and len(entry["backend"]) > 1 else entry["backend"][0]
            path = os.path.normpath(os.path.join(addon_dir, first))
            if not path.startswith(os.path.abspath(addon_dir) + os.sep):
                errs.append("entry.backend must stay inside the addon directory")
            elif not os.path.exists(path):
                errs.append(f"entry.backend file not found: {first}")
    if m.get("activation", "startup") not in ("startup", "onDemand"):
        errs.append("activation must be 'startup' or 'onDemand'")
    prov = m.get("provides", [])
    if not isinstance(prov, list) or not all(isinstance(p, str) for p in prov):
        errs.append("provides must be a list of method names")
    for s in m.get("settings", []) or []:
        if not isinstance(s, dict) or "key" not in s or s.get("type") not in SETTING_TYPES:
            errs.append(f"invalid setting definition: {s!r}")
        elif s["type"] == "choice" and not s.get("choices"):
            errs.append(f"choice setting '{s['key']}' needs choices")
    for sc in m.get("shortcuts", []) or []:
        if not isinstance(sc, dict) or "id" not in sc or "name" not in sc:
            errs.append(f"invalid shortcut definition: {sc!r}")
    return errs


def load(addon_dir):
    path = os.path.join(addon_dir, "manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except FileNotFoundError:
        raise ManifestError("manifest.json not found")
    except ValueError as e:
        raise ManifestError(f"manifest.json is not valid JSON: {e}")
    errs = validate(m, addon_dir)
    if errs:
        raise ManifestError("; ".join(errs))
    return m
