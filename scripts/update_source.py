#!/usr/bin/env python3
"""Sync source.json with the upstream repo's GitHub releases.

For every release IPA not already in source.json, download it, read the
app's Info.plist, and add a version entry. Stdlib only.
"""
import json
import os
import plistlib
import re
import sys
import tempfile
import urllib.request
import zipfile

UPSTREAM = os.environ.get("UPSTREAM_REPO", "camhoc/Madeira")
SOURCE_PATH = os.environ.get("SOURCE_PATH", "source.json")
MAX_VERSIONS = int(os.environ.get("MAX_VERSIONS", "10"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")

APP_PLIST = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")


def api(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "madeira-source-updater",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def get_releases():
    return api(f"https://api.github.com/repos/{UPSTREAM}/releases?per_page=30")


def read_ipa_info(url):
    with tempfile.NamedTemporaryFile(suffix=".ipa") as tmp:
        req = urllib.request.Request(url, headers={"User-Agent": "madeira-source-updater"})
        with urllib.request.urlopen(req) as r:
            while chunk := r.read(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        with zipfile.ZipFile(tmp.name) as z:
            name = next(n for n in z.namelist() if APP_PLIST.match(n))
            return plistlib.loads(z.read(name))


def short_notes(body, limit=1500):
    body = (body or "").strip()
    return body if len(body) <= limit else body[:limit].rstrip() + "…"


def main():
    with open(SOURCE_PATH, encoding="utf-8") as f:
        source = json.load(f)
    app = source["apps"][0]
    versions = app.get("versions", [])
    known = {v["downloadURL"] for v in versions}

    added = 0
    for rel in get_releases():
        if rel.get("draft"):
            continue
        for asset in rel.get("assets", []):
            if not asset["name"].lower().endswith(".ipa"):
                continue
            url = asset["browser_download_url"]
            if url in known:
                continue
            print(f"New IPA: {asset['name']} ({rel['tag_name']})")
            info = read_ipa_info(url)
            bundle_id = info.get("CFBundleIdentifier")
            if bundle_id != app["bundleIdentifier"]:
                print(f"  bundle ID changed: {app['bundleIdentifier']} -> {bundle_id}")
                app["bundleIdentifier"] = bundle_id
            entry = {
                "version": info.get("CFBundleShortVersionString", "0"),
                "buildVersion": str(info.get("CFBundleVersion", "0")),
                "marketingVersion": rel.get("name") or rel["tag_name"],
                "date": rel.get("published_at") or rel.get("created_at"),
                "localizedDescription": short_notes(rel.get("body")) or rel["tag_name"],
                "downloadURL": url,
                "size": asset["size"],
            }
            if info.get("MinimumOSVersion"):
                entry["minOSVersion"] = info["MinimumOSVersion"]
            versions.append(entry)
            known.add(url)
            added += 1

    if not added:
        print("No new releases.")
        return 0

    versions.sort(key=lambda v: v["date"], reverse=True)
    app["versions"] = versions[:MAX_VERSIONS]
    with open(SOURCE_PATH, "w", encoding="utf-8") as f:
        json.dump(source, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Added {added} version(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
