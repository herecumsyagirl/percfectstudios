from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, session, Response, send_from_directory
from urllib.parse import urlparse
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from supabase import create_client, Client
from functools import wraps
import os
import base64
import uuid
import datetime
import requests
import stripe
from io import BytesIO
from PIL import Image, ImageOps
from dotenv import load_dotenv

from perchance_princess import (
    PERCHANCE_URL as PRINCESS_PERCHANCE_URL,
    PRINCESSES,
    DEFAULT_COUNT as PRINCESS_DEFAULT_COUNT,
    DEFAULT_SIZE as PRINCESS_DEFAULT_SIZE,
    NEG_PROMPT as PRINCESS_NEG_PROMPT,
    normalize_princess_key,
    build_princess_prompts,
)

# Let Pillow decode iPhone HEIC/HEIF photos. Safe no-op if the package
# is unavailable for some reason (we still handle JPEG/PNG/WebP natively).
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "percfect-secret-key")
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)

# Serve static files reliably under gunicorn
from whitenoise import WhiteNoise
app.wsgi_app = WhiteNoise(app.wsgi_app, root=os.path.join(os.path.dirname(__file__), 'static'), prefix='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ── Supabase ──────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── xAI ───────────────────────────────────────────────────
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"
FAL_KEY = os.getenv("FAL_KEY")
FAL_MODEL = "fal-ai/flux/schnell"

# ── Stripe ────────────────────────────────────────────────
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

GUEST_PRINCESS_DAILY_LIMIT = 5

PRINCESS_CHARACTERS = {
    "ariel": "Ariel-inspired mermaid princess, flowing vibrant red hair, seashell crown, ocean sparkle",
    "belle": "Belle-inspired princess, golden ball gown, warm brown hair, elegant and bookish charm",
    "jasmine": "Jasmine-inspired princess, teal outfit, long black hair, Arabian palace aesthetic",
    "rapunzel": "Rapunzel-inspired princess, impossibly long golden braid, purple dress, lantern glow",
    "elsa": "Elsa-inspired ice queen princess, shimmering blue gown, snowflake magic, platinum braid",
    "aurora": "Aurora-inspired sleeping beauty princess, pink flowing gown, soft rose-gold hair",
    "cinderella": "Cinderella-inspired princess, sparkling blue ball gown, glass slippers, midnight magic",
    "moana": "Moana-inspired island princess, ocean waves, bold spirit, tropical flower accents",
    "tiana": "Tiana-inspired princess, emerald green gown, New Orleans elegance, warm golden lighting",
    "mulan": "Mulan-inspired warrior princess, crimson and gold, fierce grace, cherry blossom petals",
}

PRINCESS_SCENES = {
    "castle": "inside a grand fairy tale castle ballroom with crystal chandeliers",
    "forest": "enchanted forest clearing with glowing fireflies and magical mist",
    "garden": "royal rose garden at golden hour with butterflies and soft petals",
    "ocean": "moonlit ocean shore with bioluminescent waves and starry sky",
    "ballroom": "lavish palace ballroom during a royal celebration",
    "throne": "ornate throne room with stained glass windows and velvet banners",
}

PRINCESS_STYLES = {
    "3d": "highly detailed 3D CGI render, cinematic volumetric lighting, ultra realistic skin texture, 8k masterpiece",
    "anime": "anime illustration style, vibrant colors, clean linework, studio quality shading",
    "fantasy": "epic fantasy art, painterly brushstrokes, dramatic rim lighting, storybook illustration",
    "cinematic": "cinematic portrait photography, shallow depth of field, film grain, golden hour glow",
}

# ── Percfect Characters ───────────────────────────────────
# Suffix appended to a user's text description (text → character image).
CHARACTER_TEXT_SUFFIX = (
    ", full body view, casual standing pose, plain white background, "
    "high quality 3D animated Pixar-style character, smooth shading, "
    "professional 3D render, clean character design"
)

# Prompt used when the user uploads a reference photo (image → character image).
CHARACTER_IMAGE_PROMPT = (
    "Transform the character into a clean, high-quality, 3D-style illustration on a "
    "pure white background. Show full body, standing straight in casual pose. Keep the "
    "exact face, hairstyle, clothing, colors, and proportions as the original character. "
    "High detail, sharp lines, consistent lighting, no distortion. Full body, professional "
    "character design like a modern animated video game render."
)

# Prompt for the 360° character-select spin video.
CHARACTER_SPIN_PROMPT = (
    "Create a smooth, slow-motion 360-degree rotation of the character on a clean white "
    "background. The character should rotate like a 3D model on a video game character "
    "selection screen. Keep perfectly consistent in face, body, clothing, and proportions "
    "during every angle. High-quality animation, smooth movement, slow speed, completing one "
    "full 360-degree slow-motion rotation."
)

# Two-character action videos. Each uses both approved character images (composited
# side-by-side into one frame) plus the action prompt below, sent to the video generator.
_ACTION_BASE = (
    "The characters from the image come together in a single shared scene {bg}. "
    "{action} Smooth cinematic animation, fluid natural motion, keep each "
    "character perfectly consistent in face, body, clothing and proportions, high quality "
    "3D animated style."
)

# Arena background presets shown in the dropdown. `scene` is woven into the video
# prompt. Drop a matching image at static/backgrounds/<key>.jpg to turn any preset
# into a real backdrop the characters get composited onto (auto-detected at runtime).
ARENA_BACKGROUNDS = [
    {"key": "studio", "label": "Clean Studio",     "scene": "in a clean, softly lit photo studio"},
    {"key": "arena",  "label": "Battle Arena",      "scene": "in a grand battle arena with a cheering crowd"},
    {"key": "throne", "label": "Throne Room",       "scene": "in an ornate royal throne room with banners and golden pillars"},
    {"key": "neon",   "label": "Neon City",         "scene": "on a futuristic neon-lit city street at night"},
    {"key": "forest", "label": "Enchanted Forest",  "scene": "in a lush enchanted forest with sunbeams through the trees"},
    {"key": "beach",  "label": "Beach Sunset",      "scene": "on a tropical beach at golden-hour sunset"},
    {"key": "space",  "label": "Outer Space",       "scene": "in deep outer space among stars and distant nebulae"},
    {"key": "ring",   "label": "Boxing Ring",       "scene": "in a professional boxing ring under bright spotlights"},
]
ARENA_BG_BY_KEY = {b["key"]: b for b in ARENA_BACKGROUNDS}
CHARACTER_ACTIONS = {
    "fight":    {"label": "Fight",      "emoji": "🥊", "action": "They engage in an energetic, dynamic martial-arts fight with dodging, striking and acrobatic action poses."},
    "romance":  {"label": "Romance",    "emoji": "💕", "action": "They share a tender romantic moment, drawing close and gazing at each other with gentle affectionate movement."},
    "dance":    {"label": "Dance",      "emoji": "💃", "action": "They dance together joyfully and in sync, with flowing, graceful, rhythmic movement."},
    "hug":      {"label": "Hug",        "emoji": "🤗", "action": "They walk towards each other and are immeadiatly overcome with aimalistic arousal."},
    "highfive": {"label": "High Five",  "emoji": "🙌", "action": "They celebrate together with an enthusiastic intimate affait, bouncy movement."},
    "faceoff":  {"label": "Face-Off",   "emoji": "⚔️", "action": "They face off in a dramatic stare-down before an epic showdown, with intense cinematic tension."},
}


def character_action_prompt(action_key: str, scene: str = None) -> str:
    act = CHARACTER_ACTIONS.get(action_key)
    if not act:
        return ""
    bg = scene.strip() if scene and scene.strip() else "on a clean background"
    return _ACTION_BASE.format(action=act["action"], bg=bg)


def _gender_word(gender: str) -> str:
    g = (gender or "").strip().lower()
    if g in ("male", "m", "man", "boy"):
        return "male"
    if g in ("female", "f", "woman", "girl"):
        return "female"
    return ""


# The 4 profile shots, in order. Index 0 (front) is the approved shot; the rest are
# image-edits of it so the character stays identical from every angle.
CHARACTER_POSES = [
    ("front", "full front view, facing the camera"),
    ("back",  "full back view, turned around facing directly away from the camera, showing the back of the head and body"),
    ("left",  "full left-side profile view"),
    ("right", "full right-side profile view"),
]


def pose_edit_prompt(pose_desc: str) -> str:
    return (
        "Show the EXACT same character — identical face, hairstyle, outfit, colors and "
        f"proportions — in a {pose_desc}. Full body, standing, plain white background, "
        "clean high-quality 3D animated style, consistent lighting, no distortion."
    )


def build_character_prompt(description: str, gender: str, has_image: bool) -> str:
    """Compose the image prompt from gender + (description | uploaded photo)."""
    g = _gender_word(gender)
    if has_image:
        prompt = CHARACTER_IMAGE_PROMPT
        if g:
            prompt += f" The character is {g}."
        return prompt
    base = description.strip()
    if g:
        base = f"{base}, a {g} character"
    return base + CHARACTER_TEXT_SUFFIX


def _load_image_any(url: str) -> Image.Image:
    """Load an image from a data: URI or an http(s) URL into a PIL image."""
    if url.startswith("data:"):
        _, b64 = url.split(",", 1)
        raw = base64.b64decode(b64)
    else:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        raw = resp.content
    return Image.open(BytesIO(raw)).convert("RGB")


def composite_characters(urls: list) -> str:
    """Place N approved character images side-by-side on a white frame and return a data URI.

    The video generator only accepts one source image, so multiple characters must share a
    single frame before they can be animated together.
    """
    target_h = 768
    frames = []
    for u in urls:
        if not u:
            continue
        im = _load_image_any(u)
        w = max(1, int(im.width * target_h / im.height))
        frames.append(im.resize((w, target_h)))
    if not frames:
        raise ValueError("No images to composite.")
    gap = 24
    total_w = sum(f.width for f in frames) + gap * (len(frames) - 1)
    canvas = Image.new("RGB", (total_w, target_h), (255, 255, 255))
    x = 0
    for f in frames:
        canvas.paste(f, (x, 0))
        x += f.width + gap
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def composite_two_characters(url_a: str, url_b: str) -> str:
    return composite_characters([url_a, url_b])


def _key_out_white(im: Image.Image, thresh: int = 32) -> Image.Image:
    """Make the border-connected near-white background transparent via flood-fill from
    the four corners. Interior white (e.g. white clothing) is preserved, so a character
    on a white card can be dropped onto a scene without an ugly white box."""
    from PIL import ImageDraw
    im = im.convert("RGBA")
    rgb = im.convert("RGB")
    SENT = (0, 255, 1)  # sentinel colour unlikely to occur in the art
    for corner in [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]:
        try:
            ImageDraw.floodfill(rgb, corner, SENT, thresh=thresh)
        except Exception:
            pass
    pr, px = rgb.load(), im.load()
    for y in range(im.height):
        for x in range(im.width):
            if pr[x, y] == SENT:
                r, g, b, _ = px[x, y]
                px[x, y] = (r, g, b, 0)
    return im


def _fit_cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop an image to exactly cover w×h (like CSS background-size:cover)."""
    im = im.convert("RGBA")
    scale = max(w / im.width, h / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    x = (im.width - w) // 2
    y = (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def composite_on_background(char_urls: list, bg_image: Image.Image) -> str:
    """Place the characters (white background keyed out) onto a scene image, standing
    side-by-side along the bottom, and return a data URI for the video generator."""
    W, H = 1024, 576
    canvas = _fit_cover(bg_image, W, H)
    char_h = int(H * 0.86)
    cut = []
    for u in char_urls:
        if not u:
            continue
        keyed = _key_out_white(_load_image_any(u))
        w = max(1, int(keyed.width * char_h / keyed.height))
        cut.append(keyed.resize((w, char_h), Image.LANCZOS))
    if not cut:
        raise ValueError("No images to composite.")
    gap = 24
    total_w = sum(c.width for c in cut) + gap * (len(cut) - 1)
    x = (W - total_w) // 2
    y = H - char_h
    for c in cut:
        canvas.alpha_composite(c, (max(0, x), y))
        x += c.width + gap
    buf = BytesIO()
    canvas.convert("RGB").save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def generate_character_shots(prompt: str, image_url: str = None, count: int = 4) -> list:
    """Generate `count` character shots. Text mode tries a single n=count call and falls
    back to looping; upload (edit) mode loops since edits don't reliably honor n."""
    urls = []
    if image_url:
        for _ in range(count):
            try:
                r = generate_image_xai(prompt, image_url)
                urls.append(r["data"][0]["url"])
            except Exception:
                break
    else:
        try:
            r = generate_image_xai(prompt, None, n=count)
            urls = [d["url"] for d in (r.get("data") or []) if d.get("url")]
        except Exception:
            urls = []
        while len(urls) < count:
            try:
                r2 = generate_image_xai(prompt, None, n=1)
                urls.append(r2["data"][0]["url"])
            except Exception:
                break
    if not urls:
        raise Exception("Image generation returned no shots.")
    return urls[:count]


# Credit packages: price_id -> (image_credits, video_credits)
CREDIT_PACKAGES = {
    "starter":   {"name": "Starter",  "price": 500,  "image_credits": 50,  "video_credits": 30,  "price_id": os.getenv("STRIPE_PRICE_STARTER")},
    "creator":   {"name": "Creator",  "price": 1500, "image_credits": 150, "video_credits": 90,  "price_id": os.getenv("STRIPE_PRICE_CREATOR")},
    "pro":       {"name": "Pro",      "price": 2500, "image_credits": 250, "video_credits": 150, "price_id": os.getenv("STRIPE_PRICE_PRO")},
}

# ── Flask-Login ───────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.unauthorized_handler
def _unauthorized():
    """Return JSON for studio API calls instead of an HTML login redirect."""
    if request.path.startswith("/percfectstudios/") or request.accept_mimetypes.best_match(
        ["application/json", "text/html"]
    ) == "application/json":
        return jsonify({"error": "Log in to continue."}), 401
    return redirect(url_for("login", next=request.path))


class User(UserMixin):
    def __init__(self, data):
        self.id = data["id"]
        self.username = data["username"]
        self.picture_credits = data.get("picture_credits", 0)
        self.video_credits = data.get("video_credits", 0)
        self.images_today = data.get("images_today", 0)
        self.videos_today = data.get("videos_today", 0)
        self.last_reset = data.get("last_reset")
        self.is_admin = bool(data.get("is_admin", False))

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    res = supabase.table("users").select("*").eq("id", user_id).single().execute()
    if res.data:
        return User(res.data)
    return None


# ── Helpers ───────────────────────────────────────────────
def reset_daily_if_needed(user_id):
    res = supabase.table("users").select("last_reset,images_today,videos_today").eq("id", user_id).single().execute()
    data = res.data
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        last = datetime.datetime.fromisoformat(str(data["last_reset"])) if data["last_reset"] else None
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        last = None
    if last is None or (now - last).days >= 1:
        supabase.table("users").update({
            "images_today": 0,
            "videos_today": 0,
            "last_reset": now.isoformat()
        }).eq("id", user_id).execute()



def _file_to_data_uri(file_storage):
    """Normalize any uploaded photo to a JPEG data URI.

    iPhones upload HEIC by default; older code just relabelled the MIME as
    JPEG, so the API received HEIC bytes claiming to be JPEG and rejected
    them. Here we actually decode the image (HEIC/PNG/WebP/etc.), fix EXIF
    rotation, downscale very large photos, and re-encode as JPEG.
    """
    data = file_storage.read()
    try:
        img = Image.open(BytesIO(data))
        # Respect the EXIF orientation iPhones write, then drop alpha.
        img = ImageOps.exif_transpose(img).convert("RGB")
        # Cap the long edge so a 12MP photo doesn't become a huge base64 blob.
        img.thumbnail((1536, 1536), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        b64 = base64.b64encode(out.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        # Fall back to the raw bytes if decoding fails for any reason.
        mime = file_storage.mimetype or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/jpeg"
        b64 = base64.b64encode(data).decode()
        return f"data:{mime};base64,{b64}"


def _get_form_image_url():
    image_url = request.form.get("image_url", "").strip() or None
    file = request.files.get("image_file")
    if file and file.filename:
        return _file_to_data_uri(file)
    return image_url


def _generate_perchance(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    negative_prompt: str = "",
) -> bytes:
    """Call Pollinations.ai (free backend Perchance uses) and return raw JPEG bytes."""
    import urllib.parse
    width = max(512, min(1536, int(width)))
    height = max(512, min(1536, int(height)))
    encoded = urllib.parse.quote(prompt, safe='')
    neg = urllib.parse.quote(negative_prompt or "", safe='')
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&model=flux&seed=-1"
    )
    if neg:
        url += f"&negative={neg}"
    resp = requests.get(url, timeout=120)
    if not resp.ok:
        raise Exception(f"Image generation error {resp.status_code}")
    return resp.content


def _is_allowed_perchance_image_url(url: str) -> bool:
    """Only accept image URLs produced by Perchance / its free backends."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    allowed = (
        "user-uploads.perchance.org",
        "user.uploads.dev",
        "image.pollinations.ai",
        "image-generation.perchance.org",
        "i.pollinations.ai",
        "cdn.pollinations.ai",
    )
    return any(host == h or host.endswith("." + h) for h in allowed)


def _perchance_store_generation(prompt: str, image_bytes: bytes, source_url: str = "") -> dict:
    """Blur, upload, and record one Perchance generation for the current user."""
    preview_bytes = _create_preview(image_bytes)
    uid = str(uuid.uuid4())
    original_path = f"{current_user.id}/{uid}_original.jpg"
    preview_path = f"{current_user.id}/{uid}_preview.jpg"
    preview_url = _upload_storage("previews", preview_path, preview_bytes)
    _upload_storage("originals", original_path, image_bytes)
    row = supabase.table("generations").insert({
        "user_id": current_user.id,
        "type": "image",
        "prompt": prompt,
        "output_url": original_path,
        "preview_url": preview_url,
        "source": "perchance",
        "unlocked": False,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }).execute()
    return {"preview_url": preview_url, "generation_id": row.data[0]["id"]}


@app.route("/studios/princess-engine")
def princess_engine():
    """Private studio engine frame — not linked publicly."""
    return send_from_directory(
        os.path.join(app.root_path, "static", "perchance"),
        "o3m0yoyo03.html",
        mimetype="text/html",
    )


def _create_preview(image_bytes: bytes) -> bytes:
    """Return a blurred, watermarked JPEG — customer sees this before paying."""
    from PIL import ImageFilter, ImageDraw, ImageFont
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=18))
    overlay = Image.new("RGBA", blurred.size, (0, 0, 0, 110))
    blurred = Image.alpha_composite(blurred.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(blurred)
    w, h = blurred.size
    text = "PERCFECT™"
    font_size = max(32, int(w * 0.11))
    font = None
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx, ty = (w - tw) // 2, (h - th) // 2
    draw.text((tx + 3, ty + 3), text, fill=(0, 0, 0, 180), font=font)
    draw.text((tx, ty), text, fill=(255, 255, 255, 230), font=font)
    out = BytesIO()
    blurred.save(out, format="JPEG", quality=82)
    return out.getvalue()


def _upload_storage(bucket: str, path: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Upload bytes to Supabase Storage; return public URL for previews, path for originals."""
    supabase.storage.from_(bucket).upload(
        path, data,
        file_options={"content-type": content_type, "upsert": "true"}
    )
    if bucket == "previews":
        return supabase.storage.from_(bucket).get_public_url(path)
    return path


def generate_image_xai(prompt: str, image_url: str = None, n: int = 1) -> dict:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    if image_url:
        payload = {
            "model": "grok-imagine-image",
            "prompt": prompt,
            "image": {"url": image_url},
        }
        endpoint = f"{XAI_BASE_URL}/images/edits"
    else:
        payload = {
            "model": "grok-imagine-image",
            "prompt": prompt,
            "n": n,
        }
        endpoint = f"{XAI_BASE_URL}/images/generations"
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
    if not resp.ok:
        raise Exception(f"xAI image error {resp.status_code}: {resp.text}")
    return resp.json()


def generate_image_fal(prompt: str, num_images: int = 10) -> list:
    """Generate images via fal.ai FLUX Schnell. Returns list of image URLs."""
    if not FAL_KEY:
        raise Exception("FAL_KEY not configured on server")
    num_images = max(1, min(10, int(num_images)))
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "num_images": num_images,
        "image_size": "square",
        "num_inference_steps": 4,
        "enable_safety_checker": False,
        "output_format": "jpeg",
    }

    resp = requests.post(f"https://queue.fal.run/{FAL_MODEL}", json=payload, headers=headers, timeout=30)
    if not resp.ok:
        raise Exception(f"fal submit error {resp.status_code}: {resp.text}")

    job = resp.json()
    if job.get("status") == "COMPLETED" or job.get("images"):
        images = job.get("images") or (job.get("response") or {}).get("images") or []
        return [img["url"] for img in images if img.get("url")]

    status_url = job.get("status_url")
    result_url = job.get("response_url")
    if not status_url or not result_url:
        raise Exception("fal queue did not return status/response URLs")

    import time
    for _ in range(90):
        time.sleep(2)
        st = requests.get(status_url, headers=headers, timeout=30)
        if not st.ok:
            continue
        status = st.json().get("status")
        if status == "COMPLETED":
            result = requests.get(result_url, headers=headers, timeout=60)
            if not result.ok:
                raise Exception(f"fal result error {result.status_code}: {result.text}")
            data = result.json()
            images = data.get("images") or (data.get("response") or {}).get("images") or []
            return [img["url"] for img in images if img.get("url")]
        if status in ("FAILED", "CANCELLED"):
            raise Exception(f"fal generation {status.lower()}")
    raise Exception("fal generation timed out")


def generate_video_xai(prompt: str, image_url: str = None, duration: int = 6) -> dict:
    import time
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "duration": duration,
        "resolution": "480p",
    }
    if image_url:
        payload["image"] = {"url": image_url}

    # Submit job
    resp = requests.post(f"{XAI_BASE_URL}/videos/generations", json=payload, headers=headers, timeout=30)
    if not resp.ok:
        raise Exception(f"xAI video error {resp.status_code}: {resp.text}")

    job = resp.json()
    job_id = job.get("request_id") or job.get("id")
    if not job_id:
        # Synchronous response with URL already
        return job

    # Poll until complete (max 3 min)
    for _ in range(36):
        time.sleep(5)
        poll = requests.get(f"{XAI_BASE_URL}/videos/{job_id}", headers=headers, timeout=15)
        if not poll.ok:
            continue
        result = poll.json()
        status = result.get("status", "")
        if status in ("done", "succeeded"):
            return result
        if status in ("failed", "cancelled"):
            raise Exception(f"Video generation {status}: {result.get('error', '')}")

    raise Exception("Video generation timed out after 3 minutes.")


# ── Auth Routes ───────────────────────────────────────────
def _user_is_adult(birthday: str) -> bool:
    if not birthday:
        return False
    try:
        born = datetime.date.fromisoformat(birthday)
        today = datetime.date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return age >= 18
    except Exception:
        return False


def _do_register(username, password, birthday="", ip=None, email=None):
    import re
    if not username or not password or not email:
        return None, "All fields are required."
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return None, "Enter a valid email address."
    if len(password) < 8 or not re.search(r'[A-Z]', password) \
            or not re.search(r'[0-9]', password) or not re.search(r'[^A-Za-z0-9]', password):
        return None, "Password needs 8+ characters, an uppercase letter, a number, and a symbol."
    exists = supabase.table("users").select("id").eq("username", username).execute()
    if exists.data:
        return None, "Username already taken."
    email_exists = supabase.table("users").select("id").eq("email", email).execute()
    if email_exists.data:
        return None, "An account with that email already exists."
    if ip:
        ip_exists = supabase.table("users").select("id").eq("signup_ip", ip).execute()
        if len(ip_exists.data or []) >= 3:
            return None, "Too many accounts from this network. Please log in to an existing account."
    hashed = generate_password_hash(password)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = {
        "username": username,
        "email": email,
        "password": hashed,
        "birthday": birthday or None,
        "picture_credits": 0,
        "video_credits": 0,
        "signup_ip": ip,
        "images_today": 0,
        "videos_today": 0,
        "last_reset": now,
        "api_key1": str(uuid.uuid4()),
        "api_key2": str(uuid.uuid4()),
    }
    if birthday:
        try:
            row["is_adult"] = _user_is_adult(birthday)
        except Exception:
            pass
    try:
        result = supabase.table("users").insert(row).execute()
    except Exception:
        row.pop("is_adult", None)
        try:
            result = supabase.table("users").insert(row).execute()
        except Exception as e:
            return None, f"Registration error: {str(e)}"
    if not result.data:
        return None, "Registration failed. Please try again."
    return User(result.data[0]), None


def _do_login(username, password):
    res = supabase.table("users").select("*").eq("username", username).maybe_single().execute()
    if res.data and check_password_hash(res.data["password"], password):
        return User(res.data), None
    return None, "Invalid username or password."


def _get_client_ip():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    return ip.split(",")[0].strip() if ip else None


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user, err = _do_register(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            request.form.get("birthday", ""),
            ip=_get_client_ip(),
            email=request.form.get("email", "").strip().lower(),
        )
        if err:
            flash(err, "danger")
            return redirect(url_for("register"))
        login_user(user, remember=True)
        return redirect(url_for("percfectstudios"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user, err = _do_login(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
        )
        if err:
            flash(err, "danger")
        else:
            login_user(user, remember=True)
            return redirect(url_for("percfectstudios"))
    return render_template("login.html")


@app.route("/auth/register", methods=["POST"])
def auth_register_ajax():
    data = request.get_json() or {}
    user, err = _do_register(
        data.get("username", "").strip(),
        data.get("password", ""),
        data.get("birthday", ""),
        ip=_get_client_ip(),
        email=data.get("email", "").strip().lower(),
    )
    if err:
        return jsonify({"error": err}), 400
    login_user(user, remember=True)
    return jsonify({"ok": True, "username": user.username,
                    "picture_credits": user.picture_credits,
                    "video_credits": user.video_credits})


@app.route("/auth/login", methods=["POST"])
def auth_login_ajax():
    data = request.get_json() or {}
    user, err = _do_login(
        data.get("username", "").strip(),
        data.get("password", ""),
    )
    if err:
        return jsonify({"error": err}), 401
    login_user(user, remember=True)
    return jsonify({"ok": True, "username": user.username,
                    "picture_credits": user.picture_credits,
                    "video_credits": user.video_credits})


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# ── Health ping (UptimeRobot keeps Render awake) ─────────
SHARE_MEDIA_HOST_SUFFIXES = ("x.ai", "supabase.co")


def _twitter_handle(user_data):
    raw = (user_data or {}).get("social_twitter", "")
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("http"):
        part = raw.rstrip("/").split("/")[-1]
        return part.lstrip("@")
    return raw.lstrip("@")


def _is_allowed_share_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc.lower()
        return any(host == suffix or host.endswith("." + suffix) for suffix in SHARE_MEDIA_HOST_SUFFIXES)
    except Exception:
        return False


@app.route("/share/fetch")
def share_fetch_media():
    """Same-origin proxy so mobile Web Share can attach image/video files."""
    url = request.args.get("url", "").strip()
    if not url or not _is_allowed_share_url(url):
        return jsonify({"error": "Invalid media URL."}), 400
    try:
        upstream = requests.get(url, timeout=60, stream=True)
        if not upstream.ok:
            return jsonify({"error": "Could not fetch media."}), 502
        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        return Response(upstream.content, mimetype=content_type)
    except Exception:
        return jsonify({"error": "Fetch failed."}), 500


@app.route("/ping")
def ping():
    return "ok", 200


# ── Self keep-alive ───────────────────────────────────────
# Render's free tier sleeps after ~15 min with no inbound traffic, which
# gives the next visitor a 30–50s cold start. A background thread pings our
# own public URL every 10 min so inbound traffic never stops and it stays warm.
def _self_keepalive():
    import time
    base = os.environ.get("RENDER_EXTERNAL_URL", "https://percfectstudios.onrender.com").rstrip("/")
    url = f"{base}/ping"
    while True:
        time.sleep(600)  # 10 min — comfortably beats the ~15 min idle window
        try:
            requests.get(url, timeout=30)
        except Exception:
            pass


if os.environ.get("ENABLE_KEEPALIVE", "1") == "1":
    import threading
    threading.Thread(target=_self_keepalive, daemon=True).start()


# ── Main Pages ────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
@login_required
def dashboard():
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("*").eq("id", current_user.id).single().execute()
    user_data = res.data

    gens = supabase.table("generations")\
        .select("*")\
        .eq("user_id", current_user.id)\
        .order("created_at", desc=True)\
        .limit(12)\
        .execute()

    return render_template("dashboard.html", user=user_data, generations=gens.data)


# ── Account Settings ─────────────────────────────────────
SETTINGS_USER_FIELDS = (
    "username,email,display_name,birthday,picture_credits,video_credits,"
    "images_today,videos_today,created_at,stripe_customer_id,"
    "social_twitter,social_instagram,social_tiktok,social_youtube,social_website"
)


def _safe_user_update(user_id, updates: dict):
    payload = dict(updates)
    while payload:
        try:
            supabase.table("users").update(payload).eq("id", user_id).execute()
            return True, payload
        except Exception:
            optional = (
                "is_adult", "display_name", "stripe_customer_id",
                "social_twitter", "social_instagram", "social_tiktok",
                "social_youtube", "social_website",
            )
            removed = False
            for key in optional:
                if key in payload:
                    payload.pop(key)
                    removed = True
                    break
            if not removed:
                raise
    return False, {}


def _normalize_social(field: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if field == "social_website" and not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    if field.startswith("social_") and field != "social_website":
        handle = value.lstrip("@").split("/")[-1].split("?")[0]
        prefixes = {
            "social_twitter": "https://x.com/",
            "social_instagram": "https://instagram.com/",
            "social_tiktok": "https://tiktok.com/@",
            "social_youtube": "https://youtube.com/@",
        }
        if value.startswith("http"):
            return value
        return prefixes.get(field, "") + handle
    return value


def _load_settings_user(user_id):
    try:
        res = supabase.table("users").select(SETTINGS_USER_FIELDS).eq("id", user_id).single().execute()
        return res.data or {}
    except Exception:
        res = supabase.table("users").select(
            "username,email,birthday,picture_credits,video_credits,images_today,videos_today,created_at"
        ).eq("id", user_id).single().execute()
        return res.data or {}


@app.context_processor
def inject_share_context():
    handle = ""
    if current_user.is_authenticated:
        try:
            handle = _twitter_handle(_load_settings_user(current_user.id))
        except Exception:
            pass
    return {"twitter_handle": handle}


def _ensure_stripe_customer(user_data: dict):
    if not stripe.api_key:
        return None
    existing = user_data.get("stripe_customer_id")
    if existing:
        return existing
    try:
        customer = stripe.Customer.create(
            email=user_data.get("email") or None,
            name=user_data.get("display_name") or user_data.get("username"),
            metadata={"user_id": str(current_user.id)},
        )
        _safe_user_update(current_user.id, {"stripe_customer_id": customer.id})
        return customer.id
    except Exception:
        return None


def _get_billing_info(stripe_customer_id):
    info = {"cards": [], "payments": [], "portal_available": bool(stripe.api_key)}
    if not stripe_customer_id or not stripe.api_key:
        return info
    try:
        methods = stripe.PaymentMethod.list(customer=stripe_customer_id, type="card", limit=5)
        for pm in methods.data:
            card = pm.card
            info["cards"].append({
                "brand": (card.brand or "card").title(),
                "last4": card.last4,
                "exp": f"{card.exp_month:02d}/{str(card.exp_year)[-2:]}",
            })
    except Exception:
        pass
    try:
        sessions = stripe.checkout.Session.list(customer=stripe_customer_id, limit=8)
        for s in sessions.data:
            if s.payment_status != "paid":
                continue
            pkg = CREDIT_PACKAGES.get((s.metadata or {}).get("package", ""), {})
            info["payments"].append({
                "date": datetime.datetime.fromtimestamp(s.created, datetime.timezone.utc).strftime("%b %d, %Y"),
                "amount": f"${s.amount_total / 100:.2f}",
                "package": pkg.get("name", "Credit purchase"),
            })
    except Exception:
        pass
    return info


@app.route("/settings/billing-portal")
@login_required
def settings_billing_portal():
    user_data = _load_settings_user(current_user.id)
    customer_id = _ensure_stripe_customer(user_data)
    if not customer_id:
        flash("Billing is not available right now. Please try again later.", "danger")
        return redirect(url_for("account_settings") + "#billing")

    base_url = request.host_url.rstrip("/")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{base_url}/settings#billing",
        )
        return redirect(portal.url, code=303)
    except Exception:
        flash("Could not open billing portal. Enable Stripe Customer Portal in your Stripe dashboard.", "danger")
        return redirect(url_for("account_settings") + "#billing")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def account_settings():
    user_data = _load_settings_user(current_user.id)
    success = None
    error = None
    active_tab = request.args.get("tab", "personal")

    if request.method == "POST":
        action = request.form.get("action")
        active_tab = request.form.get("tab", active_tab)

        if action == "personal":
            display_name = request.form.get("display_name", "").strip()
            birthday = request.form.get("birthday", "").strip()
            updates = {}
            if display_name:
                updates["display_name"] = display_name[:80]
            if birthday:
                updates["birthday"] = birthday
                updates["is_adult"] = _user_is_adult(birthday)
            if updates:
                _safe_user_update(current_user.id, updates)
                user_data.update(updates)
                success = "Personal info saved."
            else:
                error = "Nothing to update."

        elif action == "account":
            import re
            email = request.form.get("email", "").strip().lower()
            username = request.form.get("username", "").strip()
            updates = {}
            if email:
                if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
                    error = "Enter a valid email address."
                else:
                    dup = supabase.table("users").select("id").eq("email", email).neq("id", current_user.id).execute()
                    if dup.data:
                        error = "That email is already in use."
                    else:
                        updates["email"] = email
            if not error and username and username != user_data.get("username"):
                if not re.match(r'^[a-zA-Z0-9_]{3,24}$', username):
                    error = "Username must be 3–24 characters (letters, numbers, underscore)."
                else:
                    dup = supabase.table("users").select("id").eq("username", username).execute()
                    if dup.data:
                        error = "That username is already taken."
                    else:
                        updates["username"] = username
            if not error and updates:
                _safe_user_update(current_user.id, updates)
                user_data.update(updates)
                success = "Account details saved."

        elif action == "social":
            updates = {}
            for field in ("social_twitter", "social_instagram", "social_tiktok", "social_youtube", "social_website"):
                updates[field] = _normalize_social(field, request.form.get(field, ""))
            _safe_user_update(current_user.id, updates)
            user_data.update(updates)
            success = "Social profiles saved."

        elif action == "promo":
            active_tab = "offers"
            result, err = _redeem_promo(current_user.id, request.form.get("code", ""))
            if err:
                error = err
            else:
                success = result["message"]
                user_data = _load_settings_user(current_user.id)

        elif action == "password":
            pw_res = supabase.table("users").select("password").eq("id", current_user.id).single().execute()
            stored_hash = (pw_res.data or {}).get("password", "")
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")
            if not check_password_hash(stored_hash, current_pw):
                error = "Current password is incorrect."
            elif len(new_pw) < 8:
                error = "New password must be at least 8 characters."
            elif new_pw != confirm_pw:
                error = "Passwords don't match."
            else:
                supabase.table("users").update({"password": generate_password_hash(new_pw)}).eq("id", current_user.id).execute()
                success = "Password updated."

    billing = _get_billing_info(user_data.get("stripe_customer_id"))
    checkout_promo = session.get("stripe_promo_code")
    return render_template(
        "settings.html",
        user=user_data,
        billing=billing,
        packages=CREDIT_PACKAGES,
        checkout_promo=checkout_promo,
        success=success,
        error=error,
        active_tab=active_tab,
    )


# ── PercfectStudios ───────────────────────────────────────
@app.route("/percfectstudios")
def percfectstudios():
    user_data = None
    if current_user.is_authenticated:
        reset_daily_if_needed(current_user.id)
        res = supabase.table("users").select("picture_credits,video_credits,images_today,videos_today").eq("id", current_user.id).single().execute()
        user_data = res.data
    return render_template("percfectstudios.html", user=user_data)


@app.route("/percfectstudios/recent")
def recent_generations():
    try:
        res = supabase.table("generations")\
            .select("output_url,prompt,type")\
            .eq("type", "image")\
            .order("created_at", desc=True)\
            .limit(9)\
            .execute()
        return jsonify(res.data or [])
    except Exception:
        return jsonify([])


def _friendly_image_error(e) -> str:
    """Turn a raw xAI exception into a clear, user-facing reason. Credits are only
    charged on success, so a failure here never costs the user a credit — say so."""
    raw = str(e).lower()
    if any(k in raw for k in ("moderat", "policy", "safety", "flagged", "nsfw", "rejected",
                              "violat", "explicit", "blocked", "content filter", "not allowed")):
        return ("That prompt was rejected by the image provider's content filter, so no image "
                "was created. You were NOT charged a credit. Try rephrasing your prompt.")
    if "timeout" in raw or "timed out" in raw:
        return "The image provider timed out, so nothing was created. You were NOT charged — please try again."
    return "The image couldn't be generated. You were NOT charged a credit. Please try again."


@app.route("/percfectstudios/generate-image", methods=["POST"])
@login_required
def generate_image():
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("picture_credits,images_today").eq("id", current_user.id).single().execute()
    user_data = res.data

    if not current_user.is_admin and user_data["picture_credits"] <= 0:
        return jsonify({"error": "No image credits remaining."}), 402

    prompt = request.form.get("prompt", "").strip()
    style = request.form.get("style", "")
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    full_prompt = f"{prompt}. Style: {style}" if style else prompt
    source_image = _get_form_image_url()

    try:
        result = generate_image_xai(full_prompt, source_image)
        data = (result or {}).get("data") or []
        image_url = data[0].get("url") if data else None
        if not image_url:
            # Provider returned no image (often a silent content rejection).
            return jsonify({
                "error": "No image was returned — this usually means the prompt was filtered. "
                         "You were NOT charged a credit. Try rephrasing your prompt.",
                "charged": False,
            }), 422

        if not current_user.is_admin:
            supabase.table("users").update({
                "picture_credits": user_data["picture_credits"] - 1,
                "images_today": user_data["images_today"] + 1
            }).eq("id", current_user.id).execute()

        supabase.table("generations").insert({
            "user_id": current_user.id,
            "type": "image",
            "prompt": full_prompt,
            "output_url": image_url,
            "created_at": datetime.datetime.utcnow().isoformat()
        }).execute()

        return jsonify({"url": image_url, "type": "image"})

    except Exception as e:
        return jsonify({"error": _friendly_image_error(e), "charged": False}), 422


@app.route("/percfectstudios/generate-batch", methods=["POST"])
@login_required
def generate_batch():
    """Generate a batch of images from one prompt. Runs concurrently, charges 1 credit
    per image that actually succeeds (failures are free), and returns all the URLs."""
    BATCH_N = 10
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("picture_credits,images_today").eq("id", current_user.id).single().execute()
    user_data = res.data

    prompt = request.form.get("prompt", "").strip()
    style = request.form.get("style", "")
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if not current_user.is_admin and user_data["picture_credits"] < BATCH_N:
        return jsonify({"error": f"A batch of {BATCH_N} needs {BATCH_N} image credits — you have "
                                 f"{user_data['picture_credits']}. Buy credits or redeem a coupon code."}), 402

    full_prompt = f"{prompt}. Style: {style}" if style else prompt
    source_image = _get_form_image_url()

    from concurrent.futures import ThreadPoolExecutor

    def _one(_i):
        try:
            r = generate_image_xai(full_prompt, source_image)
            data = (r or {}).get("data") or []
            return data[0].get("url") if data else None
        except Exception:
            return None

    urls = []
    with ThreadPoolExecutor(max_workers=BATCH_N) as ex:
        for u in ex.map(_one, range(BATCH_N)):
            if u:
                urls.append(u)

    made = len(urls)
    if made and not current_user.is_admin:
        supabase.table("users").update({
            "picture_credits": max(0, user_data["picture_credits"] - made),
            "images_today": user_data["images_today"] + made,
        }).eq("id", current_user.id).execute()

    now = datetime.datetime.utcnow().isoformat()
    for u in urls:
        try:
            supabase.table("generations").insert({
                "user_id": current_user.id, "type": "image",
                "prompt": full_prompt, "output_url": u, "created_at": now,
            }).execute()
        except Exception:
            pass

    if not made:
        return jsonify({"error": "None of the batch images generated — the prompt may have been "
                                 "filtered. You were NOT charged.", "charged": 0}), 422
    return jsonify({"urls": urls, "made": made, "failed": BATCH_N - made, "charged": made})


@app.route("/percfectstudios/generate-video", methods=["POST"])
@login_required
def generate_video():
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("video_credits,videos_today").eq("id", current_user.id).single().execute()
    user_data = res.data

    prompt = request.form.get("prompt", "").strip()
    image_url = _get_form_image_url()
    duration = int(request.form.get("duration", 6))
    duration = max(5, min(15, duration))  # clamp 5-15 seconds

    if not current_user.is_admin and user_data["video_credits"] < duration:
        return jsonify({"error": f"Not enough video seconds. You have {user_data['video_credits']}s remaining."}), 402

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    try:
        result = generate_video_xai(prompt, image_url, duration)
        video_url = (result.get("video") or {}).get("url") \
            or result.get("url") \
            or (result.get("data") or [{}])[0].get("url")

        if not current_user.is_admin:
            supabase.table("users").update({
                "video_credits": user_data["video_credits"] - duration,
                "videos_today": user_data["videos_today"] + 1
            }).eq("id", current_user.id).execute()

        supabase.table("generations").insert({
            "user_id": current_user.id,
            "type": "video",
            "prompt": prompt,
            "output_url": video_url,
            "created_at": datetime.datetime.utcnow().isoformat()
        }).execute()

        return jsonify({"url": video_url, "type": "video"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Promo / Offers ────────────────────────────────────────
def _normalize_promo_code(code: str) -> str:
    return "".join((code or "").upper().split())


def _lookup_stripe_promo(code: str):
    if not stripe.api_key:
        return None
    try:
        promos = stripe.PromotionCode.list(code=_normalize_promo_code(code), active=True, limit=1)
        if promos.data:
            return promos.data[0].id
    except Exception:
        pass
    return None


def _redeem_db_promo(user_id, code: str):
    normalized = _normalize_promo_code(code)
    if not normalized:
        return None, "Enter a promo code."

    try:
        res = supabase.table("promo_codes").select("*").eq("code", normalized).eq("active", True).maybe_single().execute()
    except Exception:
        return None, "Promo codes are not set up yet. Run the promo_codes SQL in Supabase."

    promo = res.data
    if not promo:
        return None, None

    if promo.get("expires_at"):
        try:
            expires = datetime.datetime.fromisoformat(str(promo["expires_at"]).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=datetime.timezone.utc)
            if expires < datetime.datetime.now(datetime.timezone.utc):
                return None, "This promo code has expired."
        except Exception:
            pass

    max_uses = promo.get("max_uses")
    if max_uses is not None and (promo.get("uses_count") or 0) >= max_uses:
        return None, "This promo code has reached its usage limit."

    try:
        prior = supabase.table("promo_redemptions").select("id").eq("user_id", user_id).eq("promo_code_id", promo["id"]).execute()
        if prior.data and len(prior.data) >= (promo.get("max_uses_per_user") or 1):
            return None, "You have already redeemed this code."
    except Exception:
        pass

    img = int(promo.get("image_credits") or 0)
    vid = int(promo.get("video_credits") or 0)
    if img <= 0 and vid <= 0:
        return None, "This promo code has no credits attached."

    user_res = supabase.table("users").select("picture_credits,video_credits").eq("id", user_id).single().execute()
    u = user_res.data or {}
    supabase.table("users").update({
        "picture_credits": (u.get("picture_credits") or 0) + img,
        "video_credits": (u.get("video_credits") or 0) + vid,
    }).eq("id", user_id).execute()

    try:
        supabase.table("promo_redemptions").insert({
            "user_id": user_id,
            "promo_code_id": promo["id"],
        }).execute()
        supabase.table("promo_codes").update({
            "uses_count": (promo.get("uses_count") or 0) + 1,
        }).eq("id", promo["id"]).execute()
    except Exception:
        pass

    parts = []
    if img:
        parts.append(f"{img} image credits")
    if vid:
        parts.append(f"{vid} video seconds")
    msg = f"Code redeemed! Added {' and '.join(parts)}."
    if promo.get("description"):
        msg = f"{promo['description']} — {msg}"
    return {"ok": True, "message": msg, "image_credits": img, "video_credits": vid}, None


def _redeem_promo(user_id, code: str):
    result, db_err = _redeem_db_promo(user_id, code)
    if result:
        return result, None
    if db_err and "not set up" not in db_err:
        return None, db_err

    stripe_id = _lookup_stripe_promo(code)
    if stripe_id:
        session["stripe_promo_id"] = stripe_id
        session["stripe_promo_code"] = _normalize_promo_code(code)
        session.modified = True
        return {
            "ok": True,
            "checkout_only": True,
            "message": f"Discount code {_normalize_promo_code(code)} will apply at checkout.",
        }, None

    return None, db_err or "Invalid or expired promo code."


@app.route("/redeem-promo", methods=["POST"])
@login_required
def redeem_promo():
    code = request.form.get("code", "").strip()
    if request.is_json:
        code = (request.get_json() or {}).get("code", code).strip()

    result, err = _redeem_promo(current_user.id, code)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")

    if err:
        if wants_json:
            return jsonify({"error": err}), 400
        flash(err, "danger")
        return redirect(request.referrer or url_for("buy_credits"))

    if wants_json:
        return jsonify(result)

    flash(result["message"], "success")
    dest = request.form.get("redirect") or request.referrer or url_for("buy_credits")
    if "#" in dest:
        return redirect(dest)
    tab = request.form.get("tab", "offers")
    if "settings" in dest:
        return redirect(url_for("account_settings") + f"#{tab}")
    return redirect(dest)


# ── Stripe: Buy Credits ───────────────────────────────────
@app.route("/buy-credits")
@login_required
def buy_credits():
    return render_template(
        "buy_credits.html",
        packages=CREDIT_PACKAGES,
        stripe_pk=os.getenv("STRIPE_PUBLISHABLE_KEY"),
        checkout_promo=session.get("stripe_promo_code"),
    )


@app.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    package_key = request.form.get("package")
    pkg = CREDIT_PACKAGES.get(package_key)
    if not pkg or not pkg.get("price_id"):
        flash("Invalid package.", "danger")
        return redirect(url_for("buy_credits"))

    user_data = _load_settings_user(current_user.id)
    customer_id = _ensure_stripe_customer(user_data)
    base_url = request.host_url.rstrip("/")
    checkout_kwargs = {
        "payment_method_types": ["card"],
        "line_items": [{"price": pkg["price_id"], "quantity": 1}],
        "mode": "payment",
        "success_url": f"{base_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/buy-credits",
        "metadata": {
            "user_id": str(current_user.id),
            "package": package_key,
        },
    }
    if customer_id:
        checkout_kwargs["customer"] = customer_id
    elif user_data.get("email"):
        checkout_kwargs["customer_email"] = user_data["email"]

    promo_id = session.pop("stripe_promo_id", None)
    if promo_id:
        checkout_kwargs["discounts"] = [{"promotion_code": promo_id}]
    elif request.form.get("promo_code"):
        stripe_id = _lookup_stripe_promo(request.form.get("promo_code"))
        if stripe_id:
            checkout_kwargs["discounts"] = [{"promotion_code": stripe_id}]

    session_stripe = stripe.checkout.Session.create(**checkout_kwargs)
    return redirect(session_stripe.url, code=303)


@app.route("/payment-success")
@login_required
def payment_success():
    session_id = request.args.get("session_id")
    if not session_id:
        return redirect(url_for("dashboard"))

    session = stripe.checkout.Session.retrieve(session_id)
    if session.payment_status == "paid" and str(session.metadata.get("user_id")) == str(current_user.id):
        if session.customer:
            _safe_user_update(current_user.id, {"stripe_customer_id": session.customer})
        pkg = CREDIT_PACKAGES.get(session.metadata.get("package"))
        if pkg:
            res = supabase.table("users").select("picture_credits,video_credits").eq("id", current_user.id).single().execute()
            supabase.table("users").update({
                "picture_credits": res.data["picture_credits"] + pkg["image_credits"],
                "video_credits": res.data["video_credits"] + pkg["video_credits"],
                "has_purchased": True,
            }).eq("id", current_user.id).execute()
            flash(f"Payment successful! Added {pkg['image_credits']} image credits"
                  + (f" and {pkg['video_credits']} video credits" if pkg["video_credits"] else "") + ".", "success")

    return redirect(url_for("account_settings") + "#billing")


@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if session.get("payment_status") == "paid":
            user_id = session.get("metadata", {}).get("user_id")
            package_key = session.get("metadata", {}).get("package")
            pkg = CREDIT_PACKAGES.get(package_key)
            if user_id and pkg:
                res = supabase.table("users").select("picture_credits,video_credits").eq("id", user_id).single().execute()
                if res.data:
                    supabase.table("users").update({
                        "picture_credits": res.data["picture_credits"] + pkg["image_credits"],
                        "video_credits": res.data["video_credits"] + pkg["video_credits"],
                        "has_purchased": True,
                    }).eq("id", user_id).execute()

    return "", 200


# ── Kodi page ─────────────────────────────────────────────
@app.route("/kodi-setup")
@app.route("/kodi-page")
def kodi():
    """Kodi install guide — use /kodi-setup on Vercel; /kodi serves repo zip."""
    api_key = None
    if current_user.is_authenticated:
        res = supabase.table("users").select("api_key1").eq("id", current_user.id).single().execute()
        api_key = res.data.get("api_key1") if res.data else None
    return render_template("kodi.html", api_key=api_key)


# ── API key auth decorator ────────────────────────────────
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key") or (request.get_json(silent=True) or {}).get("api_key")
        if not key:
            return jsonify({"error": "API key required"}), 401
        res = supabase.table("users").select("*")\
            .or_(f"api_key1.eq.{key},api_key2.eq.{key}")\
            .limit(1).execute()
        if not res.data:
            return jsonify({"error": "Invalid API key"}), 401
        g.api_user = res.data[0]
        return f(*args, **kwargs)
    return decorated


# ── Kodi / external API ───────────────────────────────────
@app.route("/api/generate/image", methods=["POST"])
@require_api_key
def api_generate_image():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "").strip()
    num_images = int(body.get("num_images", 10))
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    try:
        urls = generate_image_fal(prompt, num_images)
        if not urls:
            return jsonify({"error": "No images returned from fal"}), 500
        user = g.api_user
        for url in urls:
            supabase.table("generations").insert({
                "user_id": user["id"], "type": "image",
                "prompt": prompt, "output_url": url,
                "created_at": datetime.datetime.utcnow().isoformat(),
            }).execute()
        return jsonify({"urls": urls, "url": urls[0], "type": "image"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate/video", methods=["POST"])
@require_api_key
def api_generate_video():
    user = g.api_user
    if user["video_credits"] <= 0:
        return jsonify({"error": "No video credits remaining."}), 402

    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "").strip()
    image_url = body.get("image_url", "").strip() or None
    duration = int(body.get("duration", 6))
    duration = max(5, min(15, duration))
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    if user["video_credits"] < duration:
        return jsonify({"error": f"Not enough video seconds. You have {user['video_credits']}s remaining."}), 402

    try:
        result = generate_video_xai(prompt, image_url, duration)
        video_url = (result.get("video") or {}).get("url") \
            or result.get("url") \
            or (result.get("data") or [{}])[0].get("url")

        supabase.table("users").update({
            "video_credits": user["video_credits"] - duration,
            "videos_today": user["videos_today"] + 1,
        }).eq("id", user["id"]).execute()

        supabase.table("generations").insert({
            "user_id": user["id"], "type": "video",
            "prompt": prompt, "output_url": video_url,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }).execute()

        return jsonify({"url": video_url, "type": "video"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gallery", methods=["GET"])
@require_api_key
def api_gallery():
    user = g.api_user
    res = supabase.table("generations")\
        .select("*").eq("user_id", user["id"])\
        .order("created_at", desc=True).limit(50).execute()
    return jsonify(res.data)


# ── Kodi activation flow ─────────────────────────────────

@app.route("/kodi-activate")
def kodi_activate_page():
    device_code = request.args.get("device", "")
    return render_template("kodi_activate.html", device_code=device_code)


@app.route("/api/kodi/activate", methods=["POST"])
def kodi_activate_api():
    import random, string
    data = request.get_json() or {}
    action = data.get("action")
    device_code = data.get("device_code", "")

    if action == "signup":
        user, err = _do_register(
            data.get("username", "").strip(),
            data.get("password", ""),
            data.get("birthday", ""),
            ip=_get_client_ip(),
            email=data.get("email", "").strip().lower(),
        )
        if err:
            return jsonify({"error": err}), 400
    elif action == "login":
        user, err = _do_login(
            data.get("username", "").strip(),
            data.get("password", ""),
        )
        if err:
            return jsonify({"error": err}), 401
    else:
        return jsonify({"error": "Invalid action"}), 400

    try:
        # Generate 6-digit PIN
        pin = ''.join(random.choices(string.digits, k=6))
        expires = (datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(minutes=10)).isoformat()

        # Store or update kodi session
        dc = device_code or str(uuid.uuid4())
        existing = supabase.table("kodi_sessions").select("id").eq("device_code", dc).execute()
        if existing.data:
            supabase.table("kodi_sessions").update({
                "user_id": user.id, "pin": pin, "activated": True,
                "expires_at": expires,
            }).eq("device_code", dc).execute()
        else:
            supabase.table("kodi_sessions").insert({
                "device_code": dc,
                "user_id": user.id, "pin": pin, "activated": True,
                "expires_at": expires,
            }).execute()

        # Get current credits
        res = supabase.table("users").select("picture_credits,video_credits,api_key1") \
            .eq("id", user.id).single().execute()
        credits = res.data or {}

        return jsonify({
            "pin": pin,
            "username": user.username,
            "image_credits": credits.get("picture_credits", 0),
            "video_credits": credits.get("video_credits", 0),
        })
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route("/api/kodi/verify", methods=["POST"])
def kodi_verify():
    """Fire Stick calls this with device_code + PIN to get api_key."""
    data = request.get_json() or {}
    device_code = data.get("device_code", "")
    pin = data.get("pin", "")

    session = supabase.table("kodi_sessions").select("*") \
        .eq("device_code", device_code).eq("pin", pin).execute()
    if not session.data:
        return jsonify({"error": "Invalid code. Try again."}), 401

    row = session.data[0]
    # Check expiry
    expires_str = str(row["expires_at"]).replace('Z', '+00:00')
    expires = datetime.datetime.fromisoformat(expires_str)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.timezone.utc)
    if datetime.datetime.now(datetime.timezone.utc) > expires:
        return jsonify({"error": "Code expired. Please re-scan the QR code."}), 401

    # Mark activated
    supabase.table("kodi_sessions").update({"activated": True}) \
        .eq("id", row["id"]).execute()

    # Return api_key + credits
    user_res = supabase.table("users") \
        .select("api_key1,picture_credits,video_credits,username") \
        .eq("id", row["user_id"]).single().execute()
    u = user_res.data or {}

    return jsonify({
        "api_key": u.get("api_key1"),
        "username": u.get("username"),
        "image_credits": u.get("picture_credits", 0),
        "video_credits": u.get("video_credits", 0),
    })


@app.route("/api/kodi/poll/<device_code>")
def kodi_poll(device_code):
    """Fire Stick polls this while waiting for phone activation."""
    session = supabase.table("kodi_sessions").select("*") \
        .eq("device_code", device_code).execute()
    if not session.data:
        return jsonify({"status": "waiting"})

    row = session.data[0]
    expires_str = str(row["expires_at"]).replace('Z', '+00:00')
    expires = datetime.datetime.fromisoformat(expires_str)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.timezone.utc)
    if datetime.datetime.now(datetime.timezone.utc) > expires:
        return jsonify({"status": "expired"})

    if not row.get("activated"):
        return jsonify({"status": "waiting"})

    user_res = supabase.table("users") \
        .select("api_key1,picture_credits,video_credits,username") \
        .eq("id", row["user_id"]).single().execute()
    u = user_res.data or {}

    return jsonify({
        "status": "activated",
        "api_key": u.get("api_key1"),
        "username": u.get("username"),
        "image_credits": u.get("picture_credits", 0),
        "video_credits": u.get("video_credits", 0),
    })


# In-memory TV prompt relay (device_code -> prompt text)
_tv_prompts = {}


@app.route("/api/tv/prompt", methods=["POST"])
def tv_send_prompt():
    data = request.get_json(silent=True) or {}
    device_code = (data.get("device_code") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    if not device_code or not prompt:
        return jsonify({"error": "device_code and prompt required"}), 400
    _tv_prompts[device_code] = prompt
    return jsonify({"ok": True})


@app.route("/api/tv/prompt/<device_code>")
def tv_get_prompt(device_code):
    prompt = _tv_prompts.pop(device_code, None)
    if prompt:
        return jsonify({"prompt": prompt})
    return jsonify({"prompt": None})


@app.route("/api/kodi/credits")
@require_api_key
def kodi_credits():
    """Fire Stick polls this for live credit balance."""
    u = g.api_user
    return jsonify({
        "image_credits": u.get("picture_credits", 0),
        "video_credits": u.get("video_credits", 0),
        "username": u.get("username"),
    })


def _build_princess_prompt(character, scene, style, extra=""):
    parts = [
        PRINCESS_STYLES.get(style, PRINCESS_STYLES["3d"]),
        PRINCESS_CHARACTERS.get(character, ""),
        PRINCESS_SCENES.get(scene, ""),
        extra.strip(),
        "masterpiece, best quality, sharp focus, beautiful composition",
    ]
    return ", ".join(p for p in parts if p)


def _princess_guest_remaining():
    today = datetime.date.today().isoformat()
    key = f"princess_uses_{today}"
    used = int(session.get(key, 0))
    return max(0, GUEST_PRINCESS_DAILY_LIMIT - used), used


def _increment_princess_guest_usage():
    today = datetime.date.today().isoformat()
    key = f"princess_uses_{today}"
    session[key] = int(session.get(key, 0)) + 1
    session.modified = True


@app.route("/percfectpictures")
def percfect_pictures():
    return render_template("percfect_pictures.html")


@app.route("/percfectprincesses")
def percfect_princesses():
    guest_remaining = None
    if not current_user.is_authenticated:
        guest_remaining, _ = _princess_guest_remaining()
    return render_template(
        "percfect_princesses.html",
        characters=PRINCESS_CHARACTERS,
        scenes=PRINCESS_SCENES,
        styles=PRINCESS_STYLES,
        guest_remaining=guest_remaining,
        is_logged_in=current_user.is_authenticated,
    )


@app.route("/percfectprincesses/generate", methods=["POST"])
def princess_generate():
    if not current_user.is_authenticated:
        remaining, used = _princess_guest_remaining()
        if remaining <= 0:
            return jsonify({
                "error": f"Daily free limit reached ({GUEST_PRINCESS_DAILY_LIMIT}/day). Sign up free for unlimited princesses + studio access.",
                "limit_reached": True,
            }), 429

    character = request.form.get("character", "ariel")
    scene = request.form.get("scene", "castle")
    style = request.form.get("style", "3d")
    extra = request.form.get("prompt", "").strip()
    if character not in PRINCESS_CHARACTERS:
        character = "ariel"
    if scene not in PRINCESS_SCENES:
        scene = "castle"
    if style not in PRINCESS_STYLES:
        style = "3d"

    full_prompt = _build_princess_prompt(character, scene, style, extra)

    try:
        result = generate_image_xai(full_prompt)
        image_url = result["data"][0]["url"]

        if not current_user.is_authenticated:
            _increment_princess_guest_usage()
            remaining, _ = _princess_guest_remaining()
        else:
            remaining = None
            try:
                supabase.table("generations").insert({
                    "user_id": current_user.id,
                    "type": "image",
                    "prompt": full_prompt,
                    "output_url": image_url,
                    "created_at": datetime.datetime.utcnow().isoformat(),
                }).execute()
            except Exception:
                pass

        return jsonify({"url": image_url, "prompt": full_prompt, "remaining": remaining})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Percfect Characters ───────────────────────────────────
def _load_bytes(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _user_has_purchased(user_id) -> bool:
    try:
        res = supabase.table("users").select("has_purchased,is_admin").eq("id", user_id).single().execute()
        d = res.data or {}
        return bool(d.get("is_admin")) or bool(d.get("has_purchased"))
    except Exception:
        return False


def _serialize_character(c: dict) -> dict:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "gender": c.get("gender"),
        "images": c.get("images") or [],
        "primary_image_url": c.get("primary_image_url"),
        "spin_video_url": c.get("spin_video_url"),
    }


@app.route("/studios/percfectcharacter")
def percfect_character():
    user_data = None
    if current_user.is_authenticated:
        reset_daily_if_needed(current_user.id)
        res = supabase.table("users").select(
            "picture_credits,video_credits,images_today,videos_today,has_purchased"
        ).eq("id", current_user.id).single().execute()
        user_data = res.data
    return render_template("percfect_character.html", user=user_data)


@app.route("/studios/percfectcharacter/generate-one", methods=["POST"])
@login_required
def character_generate_one():
    """Step 1: generate a SINGLE shot for the user to approve (1 image credit)."""
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("picture_credits,images_today").eq("id", current_user.id).single().execute()
    u = res.data

    if not current_user.is_admin and u["picture_credits"] < 1:
        return jsonify({"error": "You need 1 image credit. Buy credits or redeem a coupon code."}), 402

    source_image = _get_form_image_url()
    description = request.form.get("description", "").strip()
    gender = request.form.get("gender", "").strip()
    if not source_image and not description:
        return jsonify({"error": "Upload a photo or describe your character."}), 400

    prompt = build_character_prompt(description, gender, bool(source_image))
    try:
        r = generate_image_xai(prompt, source_image)
        url = r["data"][0]["url"]
        if not current_user.is_admin:
            supabase.table("users").update({
                "picture_credits": max(0, u["picture_credits"] - 1),
                "images_today": u["images_today"] + 1,
            }).eq("id", current_user.id).execute()
        return jsonify({"url": url, "prompt": prompt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/studios/percfectcharacter/finalize", methods=["POST"])
@login_required
def character_finalize():
    """Step 2 (after approval): generate the other 3 shots and save the character to the
    roster. Costs 3 image credits. The 360 spin is a separate request (generate-spin) so
    neither call exceeds the proxy timeout."""
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select(
        "picture_credits,images_today"
    ).eq("id", current_user.id).single().execute()
    u = res.data

    primary = request.form.get("primary_image_url", "").strip()
    prompt = request.form.get("prompt", "").strip()
    gender = request.form.get("gender", "").strip()
    name = (request.form.get("name", "").strip() or "Unnamed Character")[:80]
    source_mode = request.form.get("source_mode", "describe").strip()
    if not primary:
        return jsonify({"error": "Approve a shot first."}), 400

    extra_n = 3
    if not current_user.is_admin and u["picture_credits"] < extra_n:
        return jsonify({"error": f"You need {extra_n} image credits to build the full character. Buy credits or redeem a coupon code."}), 402

    try:
        # other 3 shots — back / left / right, image-edits of the approved front shot.
        # Generate them CONCURRENTLY: running these xAI edits one after another blew
        # past the gunicorn worker timeout, so the worker was killed and every extra
        # shot was lost (you'd get only the front shot). In parallel the request
        # finishes in roughly the time of a single edit.
        from concurrent.futures import ThreadPoolExecutor
        extras = CHARACTER_POSES[1:]

        def _make_shot(desc):
            rr = generate_image_xai(pose_edit_prompt(desc), primary)
            return rr["data"][0]["url"]

        results = [None] * len(extras)
        shot_errors = []
        with ThreadPoolExecutor(max_workers=len(extras)) as ex:
            futs = {ex.submit(_make_shot, desc): (i, key) for i, (key, desc) in enumerate(extras)}
            for fut in futs:
                i, key = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    shot_errors.append(f"{key}: {e}")
        images = [primary] + [r for r in results if r]

        if not current_user.is_admin:
            spent_img = len(images) - 1
            supabase.table("users").update({
                "picture_credits": max(0, u["picture_credits"] - spent_img),
                "images_today": u["images_today"] + spent_img,
            }).eq("id", current_user.id).execute()

        row = supabase.table("characters").insert({
            "user_id": current_user.id, "name": name, "gender": gender,
            "images": images, "primary_image_url": primary,
            "spin_video_url": None, "prompt": prompt, "source_mode": source_mode,
        }).execute()

        resp = {"ok": True, "character": _serialize_character(row.data[0]), "images": images}
        if shot_errors:
            resp["warning"] = f"{len(shot_errors)} of {len(extras)} extra shots failed."
            resp["shot_errors"] = shot_errors
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/studios/percfectcharacter/refine", methods=["POST"])
@login_required
def character_refine():
    """Fix one of the 4 profile shots from a text note (e.g. 'ponytail not hair down').
    Edits just that pose; the frontend re-runs generate-spin afterward. Costs 1 image credit."""
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("picture_credits,images_today").eq("id", current_user.id).single().execute()
    u = res.data

    char_id = request.form.get("character_id", "").strip()
    correction = request.form.get("correction", "").strip()
    try:
        slot = int(request.form.get("slot", "-1"))
    except ValueError:
        slot = -1
    if not correction:
        return jsonify({"error": "Describe what to change."}), 400

    cres = supabase.table("characters").select("*").eq("id", char_id).eq("user_id", current_user.id).maybe_single().execute()
    char = cres.data if cres else None
    if not char:
        return jsonify({"error": "Character not found."}), 404
    images = char.get("images") or []
    if slot < 0 or slot >= len(images):
        return jsonify({"error": "Pick which photo to fix."}), 400

    if not current_user.is_admin and u["picture_credits"] < 1:
        return jsonify({"error": "You need 1 image credit. Buy credits or redeem a coupon code."}), 402

    pose_desc = CHARACTER_POSES[slot][1] if slot < len(CHARACTER_POSES) else "full body view"
    edit_prompt = (
        f"{correction}. Keep the EXACT same character and the same {pose_desc}; apply only "
        "this change, full body, plain white background, consistent face, outfit, colors and "
        "proportions, clean 3D animated style."
    )
    try:
        r = generate_image_xai(edit_prompt, images[slot])
        images[slot] = r["data"][0]["url"]
        if slot == 0:
            primary = images[0]
        else:
            primary = char.get("primary_image_url")

        if not current_user.is_admin:
            supabase.table("users").update({
                "picture_credits": max(0, u["picture_credits"] - 1),
                "images_today": u["images_today"] + 1,
            }).eq("id", current_user.id).execute()

        supabase.table("characters").update({
            "images": images, "primary_image_url": primary,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }).eq("id", char_id).execute()

        return jsonify({"ok": True, "images": images, "slot": slot})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/studios/percfectcharacter/generate-spin", methods=["POST"])
@login_required
def character_generate_spin():
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("video_credits,videos_today").eq("id", current_user.id).single().execute()
    user_data = res.data

    char_id = request.form.get("character_id", "").strip()
    cres = supabase.table("characters").select("*").eq("id", char_id).eq("user_id", current_user.id).maybe_single().execute()
    char = cres.data if cres else None
    if not char:
        return jsonify({"error": "Character not found."}), 404
    image_url = char.get("primary_image_url")
    if not image_url:
        return jsonify({"error": "This character has no image."}), 400

    duration = 6
    if not current_user.is_admin and user_data["video_credits"] < duration:
        return jsonify({"error": f"Not enough video seconds. Buy credits or redeem a coupon code."}), 402

    try:
        result = generate_video_xai(CHARACTER_SPIN_PROMPT, image_url, duration)
        video_url = (result.get("video") or {}).get("url") \
            or result.get("url") \
            or (result.get("data") or [{}])[0].get("url")

        if not current_user.is_admin:
            supabase.table("users").update({
                "video_credits": user_data["video_credits"] - duration,
                "videos_today": user_data["videos_today"] + 1,
            }).eq("id", current_user.id).execute()

        supabase.table("characters").update({
            "spin_video_url": video_url,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }).eq("id", char_id).execute()

        supabase.table("generations").insert({
            "user_id": current_user.id, "type": "video",
            "prompt": CHARACTER_SPIN_PROMPT, "output_url": video_url,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }).execute()

        return jsonify({"url": video_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/studios/percfectcharacter/edit", methods=["POST"])
@login_required
def character_edit():
    char_id = request.form.get("character_id", "").strip()
    cres = supabase.table("characters").select("*").eq("id", char_id).eq("user_id", current_user.id).maybe_single().execute()
    char = cres.data if cres else None
    if not char:
        return jsonify({"error": "Character not found."}), 404

    updates = {}
    name = request.form.get("name", "").strip()
    gender = request.form.get("gender", "").strip()
    adjust = request.form.get("adjust", "").strip()
    if name:
        updates["name"] = name[:80]
    if gender:
        updates["gender"] = gender

    if adjust:
        # Re-generate the primary image with an edit prompt (costs 1 image credit)
        reset_daily_if_needed(current_user.id)
        ures = supabase.table("users").select("picture_credits,images_today").eq("id", current_user.id).single().execute()
        u = ures.data
        if not current_user.is_admin and u["picture_credits"] < 1:
            return jsonify({"error": "You need 1 image credit to adjust the picture."}), 402
        try:
            r = generate_image_xai(adjust, char.get("primary_image_url"))
            new_url = r["data"][0]["url"]
            updates["primary_image_url"] = new_url
            imgs = char.get("images") or []
            updates["images"] = [new_url] + [i for i in imgs if i != char.get("primary_image_url")]
            if not current_user.is_admin:
                supabase.table("users").update({
                    "picture_credits": max(0, u["picture_credits"] - 1),
                    "images_today": u["images_today"] + 1,
                }).eq("id", current_user.id).execute()
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if not updates:
        return jsonify({"error": "Nothing to update."}), 400

    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = supabase.table("characters").update(updates).eq("id", char_id).execute()
    return jsonify({"ok": True, "character": _serialize_character(row.data[0])})


@app.route("/studios/percfectcharacter/characters")
@login_required
def character_list():
    res = supabase.table("characters").select("*").eq("user_id", current_user.id).order("created_at", desc=True).execute()
    chars = [_serialize_character(c) for c in (res.data or [])]
    return jsonify({"characters": chars, "can_download": _user_has_purchased(current_user.id)})


@app.route("/studios/percfectcharacter/delete", methods=["POST"])
@login_required
def character_delete():
    char_id = request.form.get("character_id", "").strip()
    supabase.table("characters").delete().eq("id", char_id).eq("user_id", current_user.id).execute()
    return jsonify({"ok": True})


@app.route("/studios/percfectcharacter/download/<int:char_id>")
@login_required
def character_download(char_id):
    import zipfile, re
    cres = supabase.table("characters").select("*").eq("id", char_id).eq("user_id", current_user.id).maybe_single().execute()
    char = cres.data if cres else None
    if not char:
        return jsonify({"error": "Character not found."}), 404
    if not _user_has_purchased(current_user.id):
        return jsonify({"error": "Buy credits to unlock downloading your character pack."}), 402

    name = (char.get("name") or "character").strip() or "character"
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "_") or "character"
    images = char.get("images") or []

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{safe}/info.txt",
                   f"Name: {name}\nGender: {char.get('gender','')}\nPrompt: {char.get('prompt','')}\n")
        for i, url in enumerate(images, 1):
            try:
                z.writestr(f"{safe}/shot{i}.jpg", _load_bytes(url))
            except Exception:
                pass
        spin = char.get("spin_video_url")
        if spin:
            try:
                z.writestr(f"{safe}/{safe}_360.mp4", _load_bytes(spin))
            except Exception:
                pass
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


@app.route("/studios/percfectcharacter/arena")
def percfect_arena():
    user_data = None
    if current_user.is_authenticated:
        reset_daily_if_needed(current_user.id)
        res = supabase.table("users").select(
            "picture_credits,video_credits,has_purchased"
        ).eq("id", current_user.id).single().execute()
        user_data = res.data
    return render_template("percfect_arena.html", user=user_data, actions=CHARACTER_ACTIONS, backgrounds=ARENA_BACKGROUNDS)


@app.route("/studios/percfectcharacter/arena/generate", methods=["POST"])
@login_required
def arena_generate():
    reset_daily_if_needed(current_user.id)
    res = supabase.table("users").select("video_credits,videos_today").eq("id", current_user.id).single().execute()
    user_data = res.data

    ids = [i.strip() for i in request.form.get("character_ids", "").split(",") if i.strip()]
    action = request.form.get("action", "")
    if len(ids) < 2:
        return jsonify({"error": "Pick at least 2 characters."}), 400
    if len(ids) > 3:
        return jsonify({"error": "The Arena supports up to 3 characters."}), 400

    # Background: dropdown preset, free-text scene, or an uploaded image. Image-backed
    # backgrounds (uploads, or presets with a static/backgrounds/<key>.jpg) get the
    # characters composited onto them; text/preset scenes are woven into the prompt.
    bg_mode = request.form.get("bg_mode", "none")
    scene = None
    bg_image = None
    if bg_mode == "preset":
        preset = ARENA_BG_BY_KEY.get(request.form.get("bg_preset", ""))
        if preset:
            scene = preset.get("scene")
            img_path = os.path.join(app.static_folder, "backgrounds", f"{preset['key']}.jpg")
            if os.path.exists(img_path):
                try:
                    bg_image = Image.open(img_path)
                except Exception:
                    bg_image = None
    elif bg_mode == "text":
        scene = request.form.get("bg_text", "").strip() or None
    elif bg_mode == "upload":
        f = request.files.get("bg_file")
        if f and f.filename:
            try:
                bg_image = _load_image_any(_file_to_data_uri(f))
            except Exception:
                return jsonify({"error": "Couldn't read that background image."}), 400

    prompt = character_action_prompt(action, scene)
    if not prompt:
        return jsonify({"error": "Pick an interaction."}), 400

    # User-chosen length (paid per second). xAI caps a single clip at 15s.
    try:
        duration = int(request.form.get("duration", 6))
    except (TypeError, ValueError):
        duration = 6
    duration = max(5, min(15, duration))
    if not current_user.is_admin and user_data["video_credits"] < duration:
        return jsonify({"error": f"This needs {duration} video seconds — you have {user_data['video_credits']}. Buy credits or redeem a coupon code."}), 402

    cres = supabase.table("characters").select("*").in_("id", ids).eq("user_id", current_user.id).execute()
    rows = {str(c["id"]): c for c in (cres.data or [])}
    primaries = [rows[i]["primary_image_url"] for i in ids if i in rows and rows[i].get("primary_image_url")]
    if len(primaries) != len(ids):
        return jsonify({"error": "One or more characters could not be loaded."}), 400

    try:
        composite = composite_on_background(primaries, bg_image) if bg_image is not None \
            else composite_characters(primaries)
        result = generate_video_xai(prompt, composite, duration)
        video_url = (result.get("video") or {}).get("url") \
            or result.get("url") \
            or (result.get("data") or [{}])[0].get("url")

        if not current_user.is_admin:
            supabase.table("users").update({
                "video_credits": user_data["video_credits"] - duration,
                "videos_today": user_data["videos_today"] + 1,
            }).eq("id", current_user.id).execute()

        supabase.table("generations").insert({
            "user_id": current_user.id, "type": "video",
            "prompt": prompt, "output_url": video_url,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }).execute()

        return jsonify({"url": video_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Perchance / Pollinations free generator ───────────────

@app.route("/percfectstudios/perchance-generate", methods=["POST"])
@login_required
def perchance_generate():
    """Generate image(s) via Pollinations.ai (free). Returns blurred previews; charges no credits."""
    princess_raw = (request.form.get("princess") or "").strip()
    princess_key = normalize_princess_key(princess_raw) if princess_raw else ""
    prompt = (request.form.get("prompt") or "").strip()

    try:
        if princess_raw:
            count = min(6, max(1, int(request.form.get("count") or PRINCESS_DEFAULT_COUNT)))
            width = height = PRINCESS_DEFAULT_SIZE
            prompts = build_princess_prompts(princess_key, count)
            neg = PRINCESS_NEG_PROMPT
        else:
            if not prompt:
                return jsonify({"error": "Prompt is required."}), 400
            ratio = (request.form.get("ratio") or "1024x1024").lower().replace(" ", "")
            if "x" in ratio:
                w_s, h_s = ratio.split("x", 1)
                width, height = int(w_s), int(h_s)
            else:
                width, height = 1024, 1024
            count = min(4, max(1, int(request.form.get("count") or 1)))
            prompts = [prompt] * count
            neg = ""

        results = []
        errors = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _download_prompt(item_prompt: str) -> tuple[str, bytes]:
            return item_prompt, _generate_perchance(item_prompt, width, height, neg)

        workers = min(3, len(prompts))
        downloaded: list[tuple[str, bytes]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_download_prompt, p) for p in prompts]
            for fut in as_completed(futures):
                try:
                    downloaded.append(fut.result())
                except Exception as item_err:
                    errors.append(str(item_err))

        for item_prompt, image_bytes in downloaded:
            try:
                results.append(_perchance_store_generation(item_prompt, image_bytes))
            except Exception as item_err:
                errors.append(str(item_err))

        if not results:
            return jsonify({"error": errors[0] if errors else "Generation failed."}), 500

        payload = {
            "results": results,
            "source": "princess" if princess_raw else "custom",
        }
        if princess_raw:
            payload["princess"] = princess_key
        if len(results) == 1:
            payload["preview_url"] = results[0]["preview_url"]
            payload["generation_id"] = results[0]["generation_id"]
        if errors:
            payload["partial_errors"] = errors
        return jsonify(payload)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/percfectstudios/preview-capture", methods=["POST"])
@login_required
def preview_capture():
    """Download a studio image and store blurred preview for paywall."""
    image_url = (request.form.get("image_url") or "").strip()
    image_data = (request.form.get("image_data") or "").strip()

    try:
        if image_data.startswith("data:"):
            _header, _comma, b64 = image_data.partition(",")
            image_bytes = base64.b64decode(b64)
        elif image_url:
            if not _is_allowed_perchance_image_url(image_url):
                return jsonify({"error": "Invalid image source."}), 400
            resp = requests.get(image_url, timeout=120, headers={"User-Agent": "PercfectStudios/1.0"})
            if not resp.ok:
                return jsonify({"error": f"Could not fetch image ({resp.status_code})."}), 502
            content_type = (resp.headers.get("content-type") or "").lower()
            if "image" not in content_type and not image_url.lower().split("?")[0].endswith(
                (".jpg", ".jpeg", ".png", ".webp")
            ):
                return jsonify({"error": "URL did not return an image."}), 400
            image_bytes = resp.content
        else:
            return jsonify({"error": "image_url required."}), 400

        prompt = "Percfect Princess Studio"
        result = _perchance_store_generation(prompt, image_bytes, source_url=image_url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/percfectstudios/perchance-capture", methods=["POST"])
@login_required
def perchance_capture_alias():
    """Legacy alias — same as preview-capture."""
    return preview_capture()


@app.route("/percfectstudios/preview-unlock", methods=["POST"])
@login_required
def preview_unlock():
    """Charge 1 picture credit and return a signed download URL for the original."""
    gen_id = (request.form.get("generation_id") or "").strip()
    if not gen_id:
        return jsonify({"error": "generation_id required."}), 400

    res = supabase.table("generations").select("*")\
        .eq("id", gen_id).eq("user_id", current_user.id)\
        .eq("source", "perchance").maybe_single().execute()
    gen = res.data
    if not gen:
        return jsonify({"error": "Generation not found."}), 404

    if gen.get("unlocked"):
        signed = supabase.storage.from_("originals").create_signed_url(
            gen["output_url"], 3600
        )
        return jsonify({"url": signed.get("signedURL") or signed.get("signed_url")})

    credits_res = supabase.table("users").select("picture_credits")\
        .eq("id", current_user.id).single().execute()
    credits = (credits_res.data or {}).get("picture_credits", 0)
    if not current_user.is_admin and credits < 1:
        return jsonify({"error": "You need 1 image credit to unlock this."}), 402

    if not current_user.is_admin:
        supabase.table("users").update({"picture_credits": credits - 1})\
            .eq("id", current_user.id).execute()

    supabase.table("generations").update({"unlocked": True})\
        .eq("id", gen_id).execute()

    signed = supabase.storage.from_("originals").create_signed_url(
        gen["output_url"], 3600
    )
    return jsonify({"url": signed.get("signedURL") or signed.get("signed_url")})


@app.route("/percfectstudios/perchance-unlock", methods=["POST"])
@login_required
def perchance_unlock_alias():
    """Legacy alias — same as preview-unlock."""
    return preview_unlock()


@app.route("/percfectpictures2.0")
@app.route("/percfectpictures2")
def percfect_pictures2():
    user_data = None
    if current_user.is_authenticated:
        reset_daily_if_needed(current_user.id)
        res = supabase.table("users").select("picture_credits,video_credits").eq(
            "id", current_user.id
        ).single().execute()
        user_data = res.data
    return render_template("percfect_pictures2.html", user=user_data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
