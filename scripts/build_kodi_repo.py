#!/usr/bin/env python3
"""Build Kodi repository zips for GitHub (kodi-dist/) and Vercel mirror."""

import hashlib
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SRC = ROOT / "static/kodi/plugin.video.percfectstudios"
REPO_SRC = ROOT / "kodi/repository.percfectai"
OUT_GITHUB = ROOT / "kodi-dist"
OUT_SITE = ROOT.parent / "percfectai-site/kodi"
ZIPS_DIR = OUT_GITHUB / "repo/zips"
PLUGIN_ID = "plugin.video.percfectstudios"
REPO_ID = "repository.percfectai"


def read_version(addon_xml_path: Path) -> str:
    text = addon_xml_path.read_text()
    m = re.search(r'<addon[^>]+version="([^"]+)"', text)
    if not m:
        raise ValueError(f"No version in {addon_xml_path}")
    return m.group(1)


def zip_dir(src: Path, dest_zip: Path):
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        dest_zip.unlink()
    folder_name = src.name
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(src.rglob("*")):
            if fp.is_file() and ".DS_Store" not in fp.name:
                arc = str(Path(folder_name) / fp.relative_to(src))
                zf.write(fp, arc)


def build_addons_xml(plugin_xml: Path) -> str:
    inner = plugin_xml.read_text().strip()
    inner = re.sub(r"<\?xml[^?]*\?>\s*", "", inner)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n' + inner + "\n</addons>\n"


def main():
    plugin_ver = read_version(PLUGIN_SRC / "addon.xml")
    repo_ver = read_version(REPO_SRC / "addon.xml")
    plugin_zip_name = f"{PLUGIN_ID}-{plugin_ver}.zip"
    repo_zip_name = f"{REPO_ID}-{repo_ver}.zip"

    if OUT_GITHUB.exists():
        shutil.rmtree(OUT_GITHUB)
    ZIPS_DIR.mkdir(parents=True)

    plugin_zip_path = ZIPS_DIR / PLUGIN_ID / plugin_zip_name
    zip_dir(PLUGIN_SRC, plugin_zip_path)

    addons_xml = build_addons_xml(PLUGIN_SRC / "addon.xml")
    (ZIPS_DIR / "addons.xml").write_text(addons_xml)
    md5 = hashlib.md5(addons_xml.encode()).hexdigest()
    (ZIPS_DIR / "addons.xml.md5").write_text(md5)

    repo_zip_path = OUT_GITHUB / repo_zip_name
    zip_dir(REPO_SRC, repo_zip_path)

    print(f"Built plugin zip: {plugin_zip_path}")
    shutil.copy2(repo_zip_path, OUT_GITHUB / "repository.zip")
    index_html = (
        "<!DOCTYPE html>\n<html><head><meta charset=\"UTF-8\">"
        "<title>PercfectAI Kodi Repository</title></head><body>\n"
        "<h1>PercfectAI Kodi Repository</h1>\n"
        "<p>Add Source in Kodi: <code>percfectai.com/kodi</code></p>\n"
        f"<ul><li><a href=\"{repo_zip_name}\">{repo_zip_name}</a></li>"
        "<li><a href=\"repository.zip\">repository.zip</a></li></ul>\n"
        "</body></html>\n"
    )
    (OUT_GITHUB / "index.html").write_text(index_html)
    if OUT_SITE.exists():
        shutil.rmtree(OUT_SITE)
    shutil.copytree(OUT_GITHUB, OUT_SITE)
    print(f"Built repo zip:   {repo_zip_path}")
    print("Kodi URL:         https://percfectai.com/kodi")
    print("Downloader URL:   https://percfectai.com/kodi/repository.zip")
    print(f"addons.xml md5:   {md5}")


if __name__ == "__main__":
    main()
