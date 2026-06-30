"""Playwright batch download from perchance.org/o3m0yoyo03."""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import requests
from PIL import Image

from perchance_princess import (
    DEFAULT_COUNT,
    PERCHANCE_URL,
    PRINCESSES,
    normalize_princess_key,
)

MIN_IMAGE_BYTES = 10_000
BATCH_TIMEOUT_SEC = 180
POLL_INTERVAL_SEC = 2

_ALLOWED_HOSTS = (
    "user-uploads.perchance.org",
    "user.uploads.dev",
    "image.pollinations.ai",
    "image-generation.perchance.org",
    "i.pollinations.ai",
    "cdn.pollinations.ai",
)


def _url_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS)


def validate_image_bytes(data: bytes, min_size: int = MIN_IMAGE_BYTES) -> bool:
    if not data or len(data) < min_size:
        return False
    try:
        img = Image.open(BytesIO(data))
        img.verify()
        fmt = (img.format or "").upper()
        return fmt in ("JPEG", "JPG", "PNG", "WEBP")
    except Exception:
        return False


def _download_url(url: str) -> Optional[bytes]:
    if not _url_allowed(url):
        return None
    try:
        resp = requests.get(url, timeout=120, headers={"User-Agent": "PercfectStudios/1.0"})
        if resp.ok and validate_image_bytes(resp.content):
            return resp.content
    except Exception:
        pass
    return None


def _data_url_to_bytes(data_url: str) -> Optional[bytes]:
    if not data_url or not data_url.startswith("data:image/"):
        return None
    try:
        _header, _comma, b64 = data_url.partition(",")
        raw = base64.b64decode(b64)
        return raw if validate_image_bytes(raw) else None
    except Exception:
        return None


async def _find_generator_frame(page):
    for sel in ("#princessInput", "#generateBtn"):
        if await page.query_selector(sel):
            return page
    for frame in page.frames:
        try:
            if await frame.query_selector("#princessInput, #generateBtn, select"):
                return frame
        except Exception:
            continue
    return None


async def _select_princess(frame, princess_key: str) -> None:
    key = normalize_princess_key(princess_key)
    desc = PRINCESSES.get(key, "")
    picked = await frame.evaluate(
        """([key, desc]) => {
            const sel = document.getElementById('princessInput') || document.querySelector('select');
            if (!sel) return false;
            const keyLow = (key || '').toLowerCase();
            const descLow = (desc || '').toLowerCase();
            for (const opt of sel.options) {
                const t = (opt.text || '').toLowerCase();
                const v = (opt.value || '').toLowerCase();
                if (t === keyLow || v === keyLow || t.includes(keyLow) || v.includes(keyLow)
                    || (descLow && (v === descLow || v.includes(descLow.slice(0, 24))))) {
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }""",
        [key, desc],
    )
    if not picked:
        raise RuntimeError(f"Could not select princess '{key}' in generator.")


async def _click_generate(frame) -> None:
    btn = await frame.query_selector("#generateBtn")
    if btn:
        await btn.click(force=True)
        return
    clicked = await frame.evaluate("""() => {
        const b = document.getElementById('generateBtn')
            || [...document.querySelectorAll('button')].find(x => (x.textContent || '').includes('Generate'));
        if (!b) return false;
        b.click();
        return true;
    }""")
    if not clicked:
        raise RuntimeError("Generate button not found on Perchance generator.")


async def _collect_dom_urls(frame) -> list:
    urls = await frame.evaluate("""() => {
        const out = [];
        const seen = new Set();
        const add = (u) => {
            if (!u || seen.has(u) || u.startsWith('data:')) return;
            seen.add(u);
            out.push(u);
        };
        document.querySelectorAll('img[src]').forEach(img => add(img.src));
        return out;
    }""")
    return [u for u in urls if _url_allowed(u)]


async def _collect_data_urls(page) -> list:
    found = []
    for frame in page.frames:
        try:
            data_url = await frame.evaluate(
                "() => typeof textToImagePluginOutput !== 'undefined'"
                " && textToImagePluginOutput.dataUrl"
                " ? textToImagePluginOutput.dataUrl : null",
                timeout=1000,
            )
            if data_url:
                raw = _data_url_to_bytes(data_url)
                if raw:
                    found.append(raw)
        except Exception:
            pass
    return found


async def _batch_async(princess_key: str, count: int = DEFAULT_COUNT) -> list:
    from playwright.async_api import async_playwright

    count = max(1, min(10, int(count)))
    url_set = set()
    byte_list = []
    seen_hashes = set()

    def _add_bytes(data: bytes) -> None:
        if not validate_image_bytes(data):
            return
        digest = str(len(data)) + data[:64].hex()
        if digest in seen_hashes:
            return
        seen_hashes.add(digest)
        byte_list.append(data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(response):
            url = response.url
            if not _url_allowed(url):
                return
            ct = (response.headers.get("content-type") or "").lower()
            if "image" in ct and response.status == 200:
                try:
                    body = await response.body()
                    _add_bytes(body)
                except Exception:
                    url_set.add(url.split("?")[0])

        page.on("response", on_response)
        await page.goto(PERCHANCE_URL, wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(4)

        frame = await _find_generator_frame(page)
        if not frame:
            await browser.close()
            raise RuntimeError("Princess generator UI not found on Perchance.")

        await _select_princess(frame, princess_key)
        await _click_generate(frame)

        elapsed = 0
        while len(byte_list) < count and elapsed < BATCH_TIMEOUT_SEC:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            elapsed += POLL_INTERVAL_SEC
            for raw in await _collect_data_urls(page):
                _add_bytes(raw)
            for url in await _collect_dom_urls(frame):
                norm = url.split("?")[0]
                if norm in url_set:
                    continue
                data = _download_url(url)
                if data:
                    url_set.add(norm)
                    _add_bytes(data)

        await browser.close()

    if len(byte_list) < count:
        raise RuntimeError(
            f"Only captured {len(byte_list)}/{count} valid images within {BATCH_TIMEOUT_SEC}s."
        )
    return byte_list[:count]


def run_princess_batch(princess_key: str, count: int = DEFAULT_COUNT) -> list:
    return asyncio.run(_batch_async(princess_key, count))
