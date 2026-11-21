#!/usr/bin/env python3
"""Sync AltStore source files with upstream GitHub releases.

Each channel is one source file tracking one upstream repo. The version
list is rebuilt from the upstream's *current* releases every run, so
deleted releases drop out. IPAs already in the file are reused without
downloading; new ones are downloaded once to read their Info.plist.
Stdlib only.
"""
import json
import os
import plistlib
import re
import sys
import tempfile
import urllib.request
import zipfile

CHANNELS = [
    {"source": "source.json", "upstream": "camhoc/Madeira"},    # Nightly
    {"source": "stable.json", "upstream": "willfaust/Madeira"}, # Stable
]
MAX_VERSIONS = int(os.environ.get("MAX_VERSIONS", "10"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
APP_PLIST = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")
UA = {"User-Agent": "madeira-source-updater"}


def api(url):
    headers = {"Accept": "application/vnd.github+json", **UA}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as r:
        return json.load(r)


def get_releases(upstream):
    return api(f"https://api.github.com/repos/{upstream}/releases?per_page=30")


def read_ipa_info(url):
    with tempfile.NamedTemporaryFile(suffix=".ipa") as tmp:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
            while chunk := r.read(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        with zipfile.ZipFile(tmp.name) as z:
            name = next(n for n in z.namelist() if APP_PLIST.match(n))
            return plistlib.loads(z.read(name))


def short_notes(body, limit=1500):
    body = (body or "").strip()
    return body if len(body) <= limit else body[:limit].rstrip() + "…"


def sync(source_path, upstream):
    print(f"== {source_path} <- {upstream}")
    with open(source_path, encoding="utf-8") as f:
        source = json.load(f)
    app = source["apps"][0]
    cached = {v["downloadURL"]: v for v in app.get("versions", [])}

    found = []  # (bundle_id, entry)
    for rel in get_releases(upstream):
        if rel.get("draft"):
            continue
        for asset in rel.get("assets", []):
            if not asset["name"].lower().endswith(".ipa"):
                continue
            url = asset["browser_download_url"]
            meta = {
                "marketingVersion": rel.get("name") or rel["tag_name"],
                "date": rel.get("published_at") or rel.get("created_at"),
                "localizedDescription": short_notes(rel.get("body")) or rel["tag_name"],
                "downloadURL": url,
                "size": asset["size"],
            }
            if url in cached:
                entry = {**cached[url], **meta}
                bundle_id = cached[url].get("_bundleID", app["bundleIdentifier"])
            else:
                print(f"  new IPA: {asset['name']} ({rel['tag_name']})")
                info = read_ipa_info(url)
                bundle_id = info.get("CFBundleIdentifier")
                entry = {
                    "version": info.get("CFBundleShortVersionString", "0"),
                    "buildVersion": str(info.get("CFBundleVersion", "0")),
                    **meta,
                }
                if info.get("MinimumOSVersion"):
                    entry["minOSVersion"] = info["MinimumOSVersion"]
            entry["_bundleID"] = bundle_id
            found.append((bundle_id, entry))

    if not found:
        print("  no IPAs in upstream releases; leaving file unchanged")
        return

    found.sort(key=lambda be: be[1]["date"], reverse=True)
    # Keep builds matching the app's bundle ID. If none match, upstream has
    # switched IDs for good, so follow the newest build's ID.
    if not any(b == app["bundleIdentifier"] for b, _ in found):
        print(f"  bundle ID changed: {app['bundleIdentifier']} -> {found[0][0]}")
        app["bundleIdentifier"] = found[0][0]
    versions = [e for b, e in found if b == app["bundleIdentifier"]][:MAX_VERSIONS]
    for b, e in found:
        if b != app["bundleIdentifier"]:
            print(f"  skipped {e['downloadURL']} (bundle ID {b})")

    # AltStore ignores unknown keys, but keep the file clean.
    for e in versions:
        e.pop("_bundleID", None)
    old = [v["downloadURL"] for v in app.get("versions", [])]
    app["versions"] = versions
    if old == [v["downloadURL"] for v in versions] and all(
        cached.get(v["downloadURL"]) == v for v in versions
    ):
        print("  no changes")
        return

    with open(source_path, "w", encoding="utf-8") as f:
        json.dump(source, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  wrote {len(versions)} version(s)")


def main():
    for ch in CHANNELS:
        sync(ch["source"], ch["upstream"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
