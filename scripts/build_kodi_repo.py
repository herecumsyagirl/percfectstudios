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
OUT_STATIC = ROOT / "static/kodi"
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


def write_dir_index(path: Path, links: list[tuple[str, str]]):
    """Apache-style listing so Kodi File Manager recognizes the folder."""
    rows = "\n".join(
        f'<tr><td><a href="{href}">{label}</a></td></tr>' for href, label in links
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!DOCTYPE html><html><head><title>Index of "
        f"{path.parent.name}/</title></head><body>\n"
        f"<h1>Index of {path.parent.name}/</h1>\n<table>{rows}</table>\n</body></html>\n"
    )


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
    shutil.copy2(plugin_zip_path, OUT_GITHUB / plugin_zip_name)
    shutil.copy2(plugin_zip_path, OUT_GITHUB / "plugin.zip")
    # Jekyll ignores .zip files on GitHub Pages without this
    (OUT_GITHUB / ".nojekyll").write_text("")
    write_dir_index(
        OUT_GITHUB / "index.html",
        [
            (repo_zip_name, repo_zip_name),
            ("repository.zip", "repository.zip"),
            ("repo/", "repo/"),
        ],
    )
    write_dir_index(
        OUT_GITHUB / "repo/index.html",
        [("zips/", "zips/")],
    )
    write_dir_index(
        ZIPS_DIR / "index.html",
        [
            ("addons.xml", "addons.xml"),
            ("addons.xml.md5", "addons.xml.md5"),
            (f"{PLUGIN_ID}/", f"{PLUGIN_ID}/"),
        ],
    )
    write_dir_index(
        ZIPS_DIR / PLUGIN_ID / "index.html",
        [(plugin_zip_name, plugin_zip_name)],
    )
    if OUT_SITE.exists():
        shutil.rmtree(OUT_SITE)
    shutil.copytree(OUT_GITHUB, OUT_SITE)
    # Copy deploy artifacts only — never wipe plugin source tree
    OUT_STATIC.mkdir(parents=True, exist_ok=True)
    for name in ("plugin.zip", plugin_zip_name, repo_zip_name, "repository.zip"):
        src = OUT_GITHUB / name
        if src.exists():
            shutil.copy2(src, OUT_STATIC / name)
    static_zips = OUT_STATIC / "repo" / "zips"
    if static_zips.exists():
        shutil.rmtree(static_zips)
    shutil.copytree(OUT_GITHUB / "repo" / "zips", static_zips)
    print(f"Built repo zip:   {repo_zip_path}")
    print("Kodi Add Source:  https://percfectai.com/kodi/")
    print("All hosting:      percfectai.com only (no GitHub)")
    print(f"addons.xml md5:   {md5}")


if __name__ == "__main__":
    main()
