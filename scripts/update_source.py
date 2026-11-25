#!/usr/bin/env python3
"""Keep the AltStore source files in sync with upstream releases.

- source.json / stable.json: Madeira nightly and stable (one app each).
- misc.json: generated from misc-apps.json, one app per upstream repo.

Version lists are rebuilt from each upstream's *current* releases every
run, so deleted releases drop out. IPAs already listed are reused without
downloading; new ones are downloaded once to read their Info.plist (and,
for misc apps, their icon). Stdlib only.
"""
import json
import os
import plistlib
import re
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ipa_icon  # noqa: E402

MADEIRA = [
    {"source": "source.json", "upstream": "github:camhoc/Madeira"},     # Nightly
    {"source": "stable.json", "upstream": "github:willfaust/Madeira"},  # Stable
]
MISC_CONFIG = "misc-apps.json"
MISC_SOURCE = "misc.json"
ICON_DIR = "icons"

MAX_VERSIONS = int(os.environ.get("MAX_VERSIONS", "10"))
MAX_MISC_VERSIONS = int(os.environ.get("MAX_MISC_VERSIONS", "5"))
PAGES_BASE = os.environ.get("PAGES_BASE", "").rstrip("/") + "/"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
APP_PLIST = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")
UA = {"User-Agent": "madeira-source-updater"}


# ---------- upstream access ----------

def fetch_json(url, github=False):
    headers = {"Accept": "application/json", **UA}
    if github:
        headers["Accept"] = "application/vnd.github+json"
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return json.load(r)


def get_releases(upstream):
    """Releases newest first. upstream is 'github:owner/repo' or
    'forgejo:host/owner/repo' (Forgejo/Gitea use the same release fields)."""
    kind, _, path = upstream.partition(":")
    if kind == "github":
        rels = fetch_json(f"https://api.github.com/repos/{path}/releases?per_page=30", github=True)
    elif kind in ("forgejo", "gitea"):
        host, repo = path.split("/", 1)
        rels = fetch_json(f"https://{host}/api/v1/repos/{repo}/releases?limit=30")
    else:
        raise ValueError(f"unknown upstream type: {upstream}")
    rels = [r for r in rels if not r.get("draft")]
    rels.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    return rels


def read_ipa(url, want_icon=False):
    with tempfile.NamedTemporaryFile(suffix=".ipa") as tmp:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
            while chunk := r.read(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        with zipfile.ZipFile(tmp.name) as z:
            name = next(n for n in z.namelist() if APP_PLIST.match(n))
            info = plistlib.loads(z.read(name))
            icon = ipa_icon.extract_icon(z, info) if want_icon else None
    return info, icon


def short_notes(body, limit=1500):
    body = (body or "").strip()
    return body if len(body) <= limit else body[:limit].rstrip() + "…"


# ---------- version list building ----------

def build_versions(upstream, asset_re, current_bundle_id, cached, cap, need_icon=False):
    """Return (bundle_id, versions, icon_png_or_None)."""
    pattern = re.compile(asset_re, re.I)
    found = []          # (bundle_id, entry)
    icon = None
    processed = 0
    for rel in get_releases(upstream):
        for asset in rel.get("assets", []):
            if not pattern.search(asset["name"]):
                continue
            url = asset["browser_download_url"]
            meta = {
                "marketingVersion": rel.get("name") or rel["tag_name"],
                "date": rel.get("published_at") or rel.get("created_at"),
                "localizedDescription": short_notes(rel.get("body")) or rel["tag_name"],
                "downloadURL": url,
                "size": asset["size"],
            }
            want_icon = need_icon and icon is None and not found
            if url in cached and not want_icon:
                entry = {**cached[url], **meta}
                bundle_id = current_bundle_id
            else:
                print(f"  reading {asset['name']} ({rel['tag_name']})")
                info, png = read_ipa(url, want_icon=want_icon)
                icon = icon or png
                bundle_id = info.get("CFBundleIdentifier")
                entry = {
                    "version": info.get("CFBundleShortVersionString", "0"),
                    "buildVersion": str(info.get("CFBundleVersion", "0")),
                    **meta,
                }
                if info.get("MinimumOSVersion"):
                    entry["minOSVersion"] = info["MinimumOSVersion"]
            found.append((bundle_id, entry))
            processed += 1
            break  # one IPA per release
        matching = sum(1 for b, _ in found if b == (current_bundle_id or found[0][0]))
        if matching >= cap or processed >= cap * 3:
            break

    if not found:
        return current_bundle_id, None, None

    target = current_bundle_id
    if not target or not any(b == target for b, _ in found):
        if target:
            print(f"  bundle ID changed: {target} -> {found[0][0]}")
        target = found[0][0]

    versions, seen = [], set()
    for b, e in found:
        vid = (e["version"], e["buildVersion"])
        if b != target:
            print(f"  skipped {e['downloadURL']} (bundle ID {b})")
        elif vid in seen:
            print(f"  skipped duplicate {vid[0]} ({vid[1]}): {e['downloadURL']}")
        else:
            seen.add(vid)
            versions.append(e)
    return target, versions[:cap], icon


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------- Madeira sources ----------

def sync_madeira(source_path, upstream):
    print(f"== {source_path} <- {upstream}")
    with open(source_path, encoding="utf-8") as f:
        source = json.load(f)
    app = source["apps"][0]
    cached = {v["downloadURL"]: v for v in app.get("versions", [])}
    bundle_id, versions, _ = build_versions(
        upstream, r"\.ipa$", app["bundleIdentifier"], cached, MAX_VERSIONS)
    if versions is None:
        print("  no IPAs found; leaving file unchanged")
        return
    if bundle_id == app["bundleIdentifier"] and versions == app.get("versions"):
        print("  no changes")
        return
    app["bundleIdentifier"] = bundle_id
    app["versions"] = versions
    write_json(source_path, source)
    print(f"  wrote {len(versions)} version(s)")


# ---------- misc source ----------

APP_FIELDS = ("name", "developerName", "subtitle", "localizedDescription", "tintColor", "category")


def sync_misc():
    print(f"== {MISC_SOURCE} <- {MISC_CONFIG}")
    with open(MISC_CONFIG, encoding="utf-8") as f:
        config = json.load(f)
    try:
        with open(MISC_SOURCE, encoding="utf-8") as f:
            old = json.load(f)
    except FileNotFoundError:
        old = {"apps": []}
    old_apps = {a.get("_upstream"): a for a in old.get("apps", [])}
    os.makedirs(ICON_DIR, exist_ok=True)

    apps = []
    for cfg in config["apps"]:
        up = cfg["upstream"]
        print(f"-- {cfg['name']} <- {up}")
        prev = old_apps.get(up, {})
        cached = {v["downloadURL"]: v for v in prev.get("versions", [])}
        bundle_id = prev.get("bundleIdentifier")
        icon_path = os.path.join(ICON_DIR, f"{bundle_id}.png") if bundle_id else None
        need_icon = not (icon_path and os.path.exists(icon_path))
        try:
            bundle_id, versions, icon = build_versions(
                up, cfg.get("asset", r"\.ipa$"), bundle_id, cached, MAX_MISC_VERSIONS, need_icon)
        except Exception as e:  # one broken upstream shouldn't sink the rest
            print(f"::warning::{cfg['name']}: {e} (kept previous entry)")
            if prev:
                apps.append(prev)
            continue
        if not versions:
            print("  no IPAs found; skipping app")
            if prev:
                apps.append(prev)
            continue
        icon_file = f"{bundle_id}.png"
        if icon:
            with open(os.path.join(ICON_DIR, icon_file), "wb") as f:
                f.write(icon)
        app = {k: cfg[k] for k in APP_FIELDS if k in cfg}
        app["bundleIdentifier"] = bundle_id
        if os.path.exists(os.path.join(ICON_DIR, icon_file)):
            app["iconURL"] = PAGES_BASE + f"{ICON_DIR}/{icon_file}"
        elif prev.get("iconURL"):
            app["iconURL"] = prev["iconURL"]
        app["screenshots"] = []
        app["versions"] = versions
        app["appPermissions"] = {"entitlements": [], "privacy": {}}
        app["_upstream"] = up  # AltStore ignores unknown keys; used to match apps between runs
        apps.append(app)

    src = dict(config["source"])
    if apps and apps[0].get("iconURL"):
        src.setdefault("iconURL", apps[0]["iconURL"])
    src["apps"] = apps
    src["news"] = []
    if src == old:
        print("  no changes")
        return
    write_json(MISC_SOURCE, src)
    print(f"  wrote {len(apps)} app(s)")


def main():
    for ch in MADEIRA:
        try:
            sync_madeira(ch["source"], ch["upstream"])
        except Exception as e:  # keep going so one outage doesn't block the rest
            print(f"::warning::{ch['source']}: {e}")
    sync_misc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
