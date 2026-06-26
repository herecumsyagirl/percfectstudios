from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, session, Response
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
from dotenv import load_dotenv

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

# Credit packages: price_id -> (image_credits, video_credits)
CREDIT_PACKAGES = {
    "starter":   {"name": "Starter",  "price": 500,  "image_credits": 50,  "video_credits": 30,  "price_id": os.getenv("STRIPE_PRICE_STARTER")},
    "creator":   {"name": "Creator",  "price": 1500, "image_credits": 150, "video_credits": 90,  "price_id": os.getenv("STRIPE_PRICE_CREATOR")},
    "pro":       {"name": "Pro",      "price": 2500, "image_credits": 250, "video_credits": 150, "price_id": os.getenv("STRIPE_PRICE_PRO")},
}

# ── Flask-Login ───────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"


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
    data = file_storage.read()
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


def generate_image_xai(prompt: str, image_url: str = None) -> dict:
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
            "n": 1,
        }
        endpoint = f"{XAI_BASE_URL}/images/generations"
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
    if not resp.ok:
        raise Exception(f"xAI image error {resp.status_code}: {resp.text}")
    return resp.json()


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
        "picture_credits": 10,
        "video_credits": 6,
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
        image_url = result["data"][0]["url"]

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
        return jsonify({"error": str(e)}), 500


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
    user = g.api_user
    if user["picture_credits"] <= 0:
        return jsonify({"error": "No image credits remaining."}), 402

    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "").strip()
    source_image = (body.get("image_url") or "").strip() or None
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    try:
        result = generate_image_xai(prompt, source_image)
        image_url = result["data"][0]["url"]

        supabase.table("users").update({
            "picture_credits": user["picture_credits"] - 1,
            "images_today": user["images_today"] + 1,
        }).eq("id", user["id"]).execute()

        supabase.table("generations").insert({
            "user_id": user["id"], "type": "image",
            "prompt": prompt, "output_url": image_url,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }).execute()

        return jsonify({"url": image_url, "type": "image"})
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
