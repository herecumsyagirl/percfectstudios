from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
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

# Serve static files reliably under gunicorn
from whitenoise import WhiteNoise
app.wsgi_app = WhiteNoise(app.wsgi_app, root=os.path.join(os.path.dirname(__file__), 'static'), prefix='static')

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
    payload = {"model": "grok-imagine-video", "prompt": prompt, "duration": duration}
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
        if ip_exists.data:
            return None, "An account already exists from this network. Please log in."
    hashed = generate_password_hash(password)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        result = supabase.table("users").insert({
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
        }).execute()
        if not result.data:
            return None, "Registration failed. Please try again."
        return User(result.data[0]), None
    except Exception as e:
        return None, f"Registration error: {str(e)}"


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
        login_user(user)
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
            login_user(user)
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
    login_user(user)
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
    login_user(user)
    return jsonify({"ok": True, "username": user.username,
                    "picture_credits": user.picture_credits,
                    "video_credits": user.video_credits})


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# ── Health ping (UptimeRobot keeps Render awake) ─────────
@app.route("/ping")
def ping():
    return "ok", 200


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


# ── Stripe: Buy Credits ───────────────────────────────────
@app.route("/buy-credits")
@login_required
def buy_credits():
    return render_template("buy_credits.html", packages=CREDIT_PACKAGES,
                           stripe_pk=os.getenv("STRIPE_PUBLISHABLE_KEY"))


@app.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    package_key = request.form.get("package")
    pkg = CREDIT_PACKAGES.get(package_key)
    if not pkg or not pkg.get("price_id"):
        flash("Invalid package.", "danger")
        return redirect(url_for("buy_credits"))

    base_url = request.host_url.rstrip("/")
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": pkg["price_id"], "quantity": 1}],
        mode="payment",
        success_url=f"{base_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/buy-credits",
        metadata={
            "user_id": str(current_user.id),
            "package": package_key,
        },
    )
    return redirect(session.url, code=303)


@app.route("/payment-success")
@login_required
def payment_success():
    session_id = request.args.get("session_id")
    if not session_id:
        return redirect(url_for("dashboard"))

    session = stripe.checkout.Session.retrieve(session_id)
    if session.payment_status == "paid" and str(session.metadata.get("user_id")) == str(current_user.id):
        pkg = CREDIT_PACKAGES.get(session.metadata.get("package"))
        if pkg:
            res = supabase.table("users").select("picture_credits,video_credits").eq("id", current_user.id).single().execute()
            supabase.table("users").update({
                "picture_credits": res.data["picture_credits"] + pkg["image_credits"],
                "video_credits": res.data["video_credits"] + pkg["video_credits"],
            }).eq("id", current_user.id).execute()
            flash(f"Payment successful! Added {pkg['image_credits']} image credits"
                  + (f" and {pkg['video_credits']} video credits" if pkg["video_credits"] else "") + ".", "success")

    return redirect(url_for("dashboard"))


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
                "user_id": user.id, "pin": pin, "activated": False,
                "expires_at": expires,
            }).eq("device_code", dc).execute()
        else:
            supabase.table("kodi_sessions").insert({
                "device_code": dc,
                "user_id": user.id, "pin": pin, "activated": False,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
