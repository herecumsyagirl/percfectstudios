from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client, Client
from functools import wraps
import os
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
    "starter":   {"name": "Starter",  "price": 500,  "image_credits": 25,  "video_credits": 0,  "price_id": os.getenv("STRIPE_PRICE_STARTER")},
    "creator":   {"name": "Creator",  "price": 1500, "image_credits": 100, "video_credits": 5,  "price_id": os.getenv("STRIPE_PRICE_CREATOR")},
    "pro":       {"name": "Pro",      "price": 2500, "image_credits": 200, "video_credits": 15, "price_id": os.getenv("STRIPE_PRICE_PRO")},
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


def generate_image_xai(prompt: str) -> dict:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-2-image",
        "prompt": prompt,
        "n": 1,
        "response_format": "url"
    }
    resp = requests.post(f"{XAI_BASE_URL}/images/generations", json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def generate_video_xai(prompt: str, image_url: str = None) -> dict:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "aurora",
        "prompt": prompt,
    }
    if image_url:
        payload["image_url"] = image_url

    resp = requests.post(f"{XAI_BASE_URL}/video/generations", json=payload, headers=headers, timeout=120)
    if not resp.ok:
        raise Exception(f"xAI video error {resp.status_code}: {resp.text}")
    return resp.json()


# ── Auth Routes ───────────────────────────────────────────
def _do_register(username, password, birthday=""):
    if not username or not password:
        return None, "Username and password are required."
    exists = supabase.table("users").select("id").eq("username", username).execute()
    if exists.data:
        return None, "Username already taken."
    hashed = generate_password_hash(password)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = supabase.table("users").insert({
        "username": username,
        "password": hashed,
        "birthday": birthday or None,
        "picture_credits": 5,
        "video_credits": 2,
        "images_today": 0,
        "videos_today": 0,
        "last_reset": now,
        "api_key1": str(uuid.uuid4()),
        "api_key2": str(uuid.uuid4()),
    }).select().execute()
    return User(result.data[0]), None


def _do_login(username, password):
    res = supabase.table("users").select("*").eq("username", username).maybe_single().execute()
    if res.data and check_password_hash(res.data["password"], password):
        return User(res.data), None
    return None, "Invalid username or password."


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user, err = _do_register(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            request.form.get("birthday", ""),
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

    try:
        result = generate_image_xai(full_prompt)
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

    if not current_user.is_admin and user_data["video_credits"] <= 0:
        return jsonify({"error": "No video credits remaining."}), 402

    prompt = request.form.get("prompt", "").strip()
    image_url = request.form.get("image_url", "").strip() or None

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    try:
        result = generate_video_xai(prompt, image_url)
        video_url = result.get("url") or result.get("data", [{}])[0].get("url")

        if not current_user.is_admin:
            supabase.table("users").update({
                "video_credits": user_data["video_credits"] - 1,
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
@app.route("/kodi")
def kodi():
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
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    try:
        result = generate_image_xai(prompt)
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
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    try:
        result = generate_video_xai(prompt, image_url)
        video_url = result.get("url") or result.get("data", [{}])[0].get("url")

        supabase.table("users").update({
            "video_credits": user["video_credits"] - 1,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
