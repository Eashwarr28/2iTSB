import os
import secrets
import random
import hashlib
import re
import requests
from datetime import datetime, timedelta, date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, Response, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

database_url = os.environ.get("DATABASE_URL", "sqlite:///2itsb.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = "llama-3.1-8b-instant"


# =========================================================
# MODELS
# =========================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    identification_id = db.Column(db.String(60), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="patient")
    patient_code = db.Column(db.String(4), nullable=True)
    reset_code = db.Column(db.String(8), nullable=True)
    reset_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    readings = db.relationship("HealthReading", backref="patient", lazy=True,
                               foreign_keys="HealthReading.patient_id")
    medications = db.relationship("Medication", backref="patient", lazy=True)
    inventory = db.relationship("InventoryItem", backref="patient", lazy=True)
    injection_logs = db.relationship("InjectionLog", backref="patient", lazy=True)
    badges = db.relationship("UserBadge", backref="user", lazy=True)
    privacy = db.relationship("PrivacySetting", backref="user", uselist=False, lazy=True)
    emergency = db.relationship("EmergencyContact", backref="user", uselist=False, lazy=True)


class HealthReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    glucose = db.Column(db.Float, nullable=True)
    glucose_unit = db.Column(db.String(10), default="mmol/L")
    context = db.Column(db.String(30), nullable=True)
    systolic = db.Column(db.Integer, nullable=True)
    diastolic = db.Column(db.Integer, nullable=True)
    spo2 = db.Column(db.Integer, nullable=True)
    temperature = db.Column(db.Float, nullable=True)
    weight = db.Column(db.Float, nullable=True)
    symptoms = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Medication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    dose_units = db.Column(db.Integer, nullable=True)
    schedule = db.Column(db.String(100), nullable=False)
    time_of_day = db.Column(db.String(10), nullable=True)
    status = db.Column(db.String(30), default="Pending")
    administered_at = db.Column(db.DateTime, nullable=True)


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    facility = db.Column(db.String(150), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.String(300), nullable=True)


class InjectionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    site = db.Column(db.String(40), nullable=False)
    checklist_ok = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ClinicalNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinician_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmergencyContact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    contact_name = db.Column(db.String(120), nullable=False)
    contact_phone = db.Column(db.String(30), nullable=False)
    doctor_name = db.Column(db.String(120), nullable=True)
    doctor_phone = db.Column(db.String(30), nullable=True)
    allergies = db.Column(db.String(300), nullable=True)
    rescue_protocol = db.Column(db.String(300), nullable=True)


class PrivacySetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    caregiver_access = db.Column(db.Boolean, default=False)
    clinician_sync = db.Column(db.Boolean, default=False)
    share_emergency = db.Column(db.Boolean, default=True)


class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    expiry = db.Column(db.Date, nullable=True)
    low_threshold = db.Column(db.Integer, default=5)


class Badge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    label = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(200), nullable=True)
    icon = db.Column(db.String(10), default="⭐")


class UserBadge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey("badge.id"), nullable=False)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow)


class QuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    played_on = db.Column(db.Date, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender = db.Column(db.String(10), default="user")
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CaregiverLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caregiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    alert_low_glucose = db.Column(db.Boolean, default=True)
    alert_missed_dose = db.Column(db.Boolean, default=True)
    alert_low_supply = db.Column(db.Boolean, default=True)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])


class DetectiveScenario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    situation = db.Column(db.Text, nullable=False)
    choice_a = db.Column(db.String(200), nullable=False)
    choice_b = db.Column(db.String(200), nullable=False)
    choice_c = db.Column(db.String(200), nullable=False)
    choice_d = db.Column(db.String(200), nullable=False)
    correct_index = db.Column(db.Integer, nullable=False)
    feedback_correct = db.Column(db.Text, nullable=False)
    feedback_wrong = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), nullable=True)


class InjectionScenario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    context_title = db.Column(db.String(120), nullable=False)
    context_blurb = db.Column(db.Text, nullable=False)


class QuizQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False)
    choice_a = db.Column(db.String(200), nullable=False)
    choice_b = db.Column(db.String(200), nullable=False)
    choice_c = db.Column(db.String(200), nullable=False)
    choice_d = db.Column(db.String(200), nullable=False)
    correct_index = db.Column(db.Integer, nullable=False)
    topic = db.Column(db.String(40), nullable=True)


class DailyCompletion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    game_code = db.Column(db.String(20), nullable=False)
    played_on = db.Column(db.Date, nullable=False, default=date.today)
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)


# =========================================================
# HELPERS
# =========================================================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_user():
    user = db.session.get(User, session["user_id"]) if "user_id" in session else None
    return {"current_user": user}


def award_badge(user_id, code):
    badge = Badge.query.filter_by(code=code).first()
    if not badge:
        return
    if not UserBadge.query.filter_by(user_id=user_id, badge_id=badge.id).first():
        db.session.add(UserBadge(user_id=user_id, badge_id=badge.id))


def generate_patient_code():
    for _ in range(200):
        code = f"{secrets.randbelow(10000):04d}"
        if not User.query.filter_by(patient_code=code).first():
            return code
    return f"{secrets.randbelow(10000):04d}"


def glucose_status(value, context):
    if value is None:
        return "Stable"
    if context == "After Meal":
        if value < 4.4:
            return "Low"
        if value > 8.5:
            return "High"
        return "Stable"
    if value < 4.4:
        return "Low"
    if value > 7.0:
        return "High"
    return "Stable"


def bp_status(sys_v, dia_v):
    if sys_v is None or dia_v is None:
        return None
    if sys_v < 120 and dia_v < 80:
        return "Optimal"
    if 120 <= sys_v <= 139 or 80 <= dia_v <= 89:
        return "Prehypertension"
    if 140 <= sys_v <= 159 or 90 <= dia_v <= 99:
        return "Stage 1"
    if 160 <= sys_v <= 179 or 100 <= dia_v <= 109:
        return "Stage 2"
    if sys_v >= 180 or dia_v >= 110:
        return "Stage 3"
    return "Normal"


def spo2_status(value):
    if value is None:
        return None
    if value >= 95:
        return "Normal"
    if value >= 91:
        return "Warning"
    return "Emergency"


def temp_status(value):
    if value is None:
        return None
    if value < 35.6:
        return "Low"
    if value > 37.8:
        return "High"
    return "Normal"


def daily_seed(salt=""):
    today = date.today().isoformat()
    raw = f"{today}::{salt}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16)


def daily_pick(pool, count, salt=""):
    if not pool:
        return []
    rng = random.Random(daily_seed(salt))
    count = min(count, len(pool))
    return rng.sample(pool, count)


def daily_shuffle(items, salt=""):
    rng = random.Random(daily_seed(salt))
    out = list(items)
    rng.shuffle(out)
    return out


def already_played(user_id, game_code):
    return DailyCompletion.query.filter_by(
        user_id=user_id, game_code=game_code, played_on=date.today()
    ).first() is not None


def record_completion(user_id, game_code, score, total):
    db.session.add(DailyCompletion(
        user_id=user_id, game_code=game_code,
        played_on=date.today(), score=score, total=total))


def update_streak_badges(user_id):
    days = {c.played_on for c in
            DailyCompletion.query.filter_by(user_id=user_id).all()}
    if not days:
        return
    streak = 0
    cur = date.today()
    while cur in days:
        streak += 1
        cur -= timedelta(days=1)
    if streak >= 3:
        award_badge(user_id, "streak_game_3")
    if streak >= 7:
        award_badge(user_id, "streak_game_7")
    if streak >= 30:
        award_badge(user_id, "streak_game_30")


def check_champion(user_id):
    required = [
        "hypo_detective", "injection_explorer", "quiz_ace", "quiz_learner",
        "streak_7", "rotation_master",
        "streak_game_3", "streak_game_7", "streak_game_30",
    ]
    earned_codes = {
        b.code for b in Badge.query.join(UserBadge, UserBadge.badge_id == Badge.id)
        .filter(UserBadge.user_id == user_id).all()
    }
    if all(code in earned_codes for code in required):
        award_badge(user_id, "insulin_champion")


# =========================================================
# LANDING / AUTH
# =========================================================
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    role_hint = request.args.get("as")
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        remember = bool(request.form.get("remember"))
        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.permanent = remember
        session["user_id"] = user.id
        session["role"] = user.role
        session["user_name"] = user.name
        flash(f"Welcome back, {user.name}.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html", role_hint=role_hint)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    lookup_result = None
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if action == "username":
            if user:
                lookup_result = f"Your username is: <strong>{user.email}</strong>"
            else:
                lookup_result = "No account found with that email."
        elif action == "password":
            if user:
                code = f"{secrets.randbelow(1000000):06d}"
                user.reset_code = code
                user.reset_expires = datetime.utcnow() + timedelta(minutes=15)
                db.session.commit()
                lookup_result = (
                    f"A password reset code has been generated: "
                    f"<strong>{code}</strong>. It is valid for 15 minutes. "
                    f"Use it on the <a href='/reset'>Reset Password</a> page."
                )
            else:
                lookup_result = "No account found with that email."

    return render_template("forgot.html", lookup_result=lookup_result)


@app.route("/reset", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("code", "").strip()
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("No account with that email.", "danger")
            return redirect(url_for("reset_password"))
        if not user.reset_code or user.reset_code != code:
            flash("Invalid reset code.", "danger")
            return redirect(url_for("reset_password"))
        if not user.reset_expires or user.reset_expires < datetime.utcnow():
            flash("Reset code has expired. Please request a new one.", "danger")
            return redirect(url_for("forgot"))
        if new_pw != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("reset_password"))
        if len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for("reset_password"))

        user.password_hash = generate_password_hash(new_pw)
        user.reset_code = None
        user.reset_expires = None
        db.session.commit()
        flash("Password reset successfully. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("reset.html")


@app.route("/register")
def register_pick():
    return render_template("register_pick.html")


@app.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ("patient", "caregiver"):
        flash("Invalid registration role.", "danger")
        return redirect(url_for("register_pick"))

    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        confirm = request.form["confirm_password"]
        phone = request.form.get("phone", "").strip() or None
        identification_id = request.form.get("identification_id", "").strip() or None
        pairing_code = request.form.get("pairing_code", "").strip()

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("register", role=role))
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register", role=role))
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for("register", role=role))

        user = User(
            name=name, email=email, phone=phone,
            identification_id=identification_id,
            password_hash=generate_password_hash(password),
            role=role,
            patient_code=generate_patient_code() if role == "patient" else None,
        )
        db.session.add(user)
        db.session.commit()
        db.session.add(PrivacySetting(user_id=user.id))
        db.session.add(EmergencyContact(
            user_id=user.id, contact_name="Not set", contact_phone="Not set"))
        db.session.commit()

        if role == "caregiver" and pairing_code:
            patient = User.query.filter_by(patient_code=pairing_code, role="patient").first()
            if patient:
                db.session.add(CaregiverLink(caregiver_id=user.id, patient_id=patient.id))
                db.session.commit()
                flash(f"Account created and linked to patient {patient.name}.", "success")
            else:
                flash("Account created, but pairing code was not found.", "warning")
        else:
            flash("Account created successfully. Please log in.", "success")

        return redirect(url_for("login"))

    return render_template("register.html", role=role)


@app.route("/check-email")
def check_email():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"available": False, "reason": "empty"})
    exists = User.query.filter_by(email=email).first() is not None
    return jsonify({"available": not exists})


# =========================================================
# DASHBOARD
# =========================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user = db.session.get(User, session["user_id"])
    if not user:
        session.clear()
        flash("Your session expired. Please log in again.", "warning")
        return redirect(url_for("login"))
    if user.role in ("doctor", "nurse"):
        return redirect(url_for("clinician_dashboard"))
    if user.role == "caregiver":
        return redirect(url_for("caregiver_dashboard"))

    readings = (HealthReading.query
                .filter_by(patient_id=user.id)
                .order_by(HealthReading.created_at.desc())
                .limit(20).all())
    medications = Medication.query.filter_by(patient_id=user.id).all()
    inventory = InventoryItem.query.filter_by(patient_id=user.id).all()
    appointments = (Appointment.query.filter_by(patient_id=user.id)
                    .order_by(Appointment.scheduled_at.asc()).all())
    emergency = EmergencyContact.query.filter_by(user_id=user.id).first()

    latest = readings[0] if readings else None
    g_status = glucose_status(latest.glucose, latest.context) if latest else None
    b_status = bp_status(latest.systolic, latest.diastolic) if latest else None
    s_status = spo2_status(latest.spo2) if latest else None
    t_status = temp_status(latest.temperature) if latest else None

    return render_template(
        "dashboard.html",
        readings=readings, medications=medications,
        inventory=inventory, appointments=appointments,
        emergency=emergency,
        g_status=g_status, b_status=b_status,
        s_status=s_status, t_status=t_status,
    )


@app.route("/sparkline/<int:patient_id>.svg")
@login_required
def sparkline(patient_id):
    readings = (HealthReading.query.filter_by(patient_id=patient_id)
                .filter(HealthReading.glucose.isnot(None))
                .order_by(HealthReading.created_at.asc()).limit(30).all())
    values = [r.glucose for r in readings]
    if not values:
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"></svg>',
                        mimetype="image/svg+xml")
    w, h, pad = 400, 90, 8
    vmin, vmax = min(values), max(values)
    span = max(vmax - vmin, 0.1)
    pts = []
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * i / max(len(values) - 1, 1)
        y = h - pad - (h - 2 * pad) * (v - vmin) / span
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><polyline fill="none" stroke="#1f4d2b" stroke-width="2" points="{poly}"/></svg>'
    return Response(svg, mimetype="image/svg+xml")


# =========================================================
# HEALTH DATA
# =========================================================
@app.route("/readings", methods=["POST"])
@login_required
def add_reading():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))

    def fnum(name):
        v = request.form.get(name)
        return float(v) if v not in (None, "", " ") else None

    glucose = fnum("glucose")
    if glucose is not None and not (1.1 <= glucose <= 33.3):
        flash("Glucose value out of plausible range (1.1-33.3 mmol/L).", "danger")
        return redirect(url_for("dashboard"))

    reading = HealthReading(
        patient_id=session["user_id"],
        glucose=glucose, glucose_unit="mmol/L",
        context=request.form.get("context") or None,
        systolic=int(request.form["systolic"]) if request.form.get("systolic") else None,
        diastolic=int(request.form["diastolic"]) if request.form.get("diastolic") else None,
        spo2=int(request.form["spo2"]) if request.form.get("spo2") else None,
        temperature=fnum("temperature"),
        weight=fnum("weight"),
        symptoms=request.form.get("symptoms", "").strip() or None,
    )
    db.session.add(reading)

    week_ago = datetime.utcnow() - timedelta(days=7)
    count = HealthReading.query.filter(
        HealthReading.patient_id == session["user_id"],
        HealthReading.created_at >= week_ago
    ).count()
    if count >= 7:
        award_badge(session["user_id"], "streak_7")

    db.session.commit()
    flash("Health reading saved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/readings/<int:reading_id>/delete", methods=["POST"])
@login_required
def delete_reading(reading_id):
    reading = db.session.get(HealthReading, reading_id)
    if not reading or reading.patient_id != session["user_id"]:
        flash("Reading not found.", "danger")
        return redirect(url_for("dashboard"))
    db.session.delete(reading)
    db.session.commit()
    flash("Reading deleted.", "success")
    return redirect(url_for("dashboard"))


# =========================================================
# MEDICATIONS / APPOINTMENTS
# =========================================================
@app.route("/medications/add", methods=["POST"])
@login_required
def add_medication():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    db.session.add(Medication(
        patient_id=session["user_id"],
        name=request.form["name"].strip(),
        dose_units=int(request.form["dose_units"]) if request.form.get("dose_units") else None,
        schedule=request.form["schedule"].strip(),
        time_of_day=request.form.get("time_of_day") or None,
    ))
    db.session.commit()
    flash("Medication reminder added.", "success")
    return redirect(url_for("dashboard"))


@app.route("/medications/<int:mid>/administer", methods=["POST"])
@login_required
def administer_medication(mid):
    med = db.session.get(Medication, mid)
    if not med or med.patient_id != session["user_id"]:
        flash("Not found.", "danger")
        return redirect(url_for("dashboard"))
    med.status = "Administered"
    med.administered_at = datetime.utcnow()
    db.session.commit()
    flash("Dose logged.", "success")
    return redirect(url_for("dashboard"))


@app.route("/appointments/add", methods=["POST"])
@login_required
def add_appointment():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    try:
        scheduled = datetime.strptime(request.form["scheduled_at"], "%Y-%m-%dT%H:%M")
    except (KeyError, ValueError):
        flash("Invalid appointment date.", "danger")
        return redirect(url_for("dashboard"))
    db.session.add(Appointment(
        patient_id=session["user_id"],
        facility=request.form["facility"].strip(),
        scheduled_at=scheduled,
        notes=request.form.get("notes", "").strip() or None,
    ))
    db.session.commit()
    flash("Appointment added.", "success")
    return redirect(url_for("dashboard"))


# =========================================================
# INJECTION LOG
# =========================================================
@app.route("/injection", methods=["GET", "POST"])
@login_required
def injection():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        site = request.form["site"]
        checklist_ok = bool(request.form.get("checklist_ok"))
        db.session.add(InjectionLog(
            patient_id=session["user_id"], site=site, checklist_ok=checklist_ok))
        distinct = {log.site for log in InjectionLog.query.filter_by(
            patient_id=session["user_id"]).all()}
        if len(distinct) >= 4:
            award_badge(session["user_id"], "rotation_master")
        db.session.commit()
        flash("Injection site logged.", "success")
        return redirect(url_for("injection"))
    logs = (InjectionLog.query.filter_by(patient_id=session["user_id"])
            .order_by(InjectionLog.created_at.desc()).limit(5).all())
    return render_template("injection.html", logs=logs)


# =========================================================
# CLINICIAN
# =========================================================
@app.route("/clinician")
@role_required("doctor", "nurse")
def clinician_dashboard():
    me = db.session.get(User, session["user_id"])
    if not me:
        session.clear()
        return redirect(url_for("login"))
    search = request.args.get("q", "").strip()
    query = User.query.filter_by(role="patient")
    if search:
        query = query.filter(
            (User.name.ilike(f"%{search}%")) |
            (User.identification_id.ilike(f"%{search}%")) |
            (User.phone.ilike(f"%{search}%"))
        )
    patients = query.all()
    patient_data = []
    for p in patients:
        latest = (HealthReading.query.filter_by(patient_id=p.id)
                  .order_by(HealthReading.created_at.desc()).first())
        patient_data.append({
            "patient": p, "latest": latest,
            "medications": Medication.query.filter_by(patient_id=p.id).all(),
        })
    return render_template("clinician.html", patient_data=patient_data, search=search)


@app.route("/clinician/note/<int:patient_id>", methods=["POST"])
@role_required("doctor", "nurse")
def add_clinical_note(patient_id):
    note = request.form.get("note", "").strip()
    if note:
        db.session.add(ClinicalNote(
            clinician_id=session["user_id"], patient_id=patient_id, note=note))
        db.session.commit()
        flash("Clinical note saved.", "success")
    return redirect(url_for("clinician_dashboard"))


@app.route("/clinician/report/<int:patient_id>")
@role_required("doctor", "nurse")
def clinician_report(patient_id):
    patient = db.session.get(User, patient_id)
    if not patient or patient.role != "patient":
        flash("Patient not found.", "danger")
        return redirect(url_for("clinician_dashboard"))

    week = request.args.get("week", 1, type=int)
    if week not in (1, 2, 3, 4):
        week = 1

    now = datetime.utcnow()
    end = now - timedelta(days=(week - 1) * 7)
    start = end - timedelta(days=7)

    readings = (HealthReading.query
                .filter(HealthReading.patient_id == patient_id,
                        HealthReading.created_at >= start,
                        HealthReading.created_at < end)
                .order_by(HealthReading.created_at.asc()).all())
    notes = (ClinicalNote.query.filter_by(patient_id=patient_id)
             .order_by(ClinicalNote.created_at.desc()).limit(10).all())
    has_older = HealthReading.query.filter(
        HealthReading.patient_id == patient_id,
        HealthReading.created_at < start).count() > 0

    points = [(r.created_at, r.glucose) for r in readings if r.glucose is not None]
    graph_svg = _build_glucose_svg(points, width=700, height=220)

    return render_template(
        "report.html",
        patient=patient, readings=readings, notes=notes,
        week=week, start=start, end=end,
        graph_svg=graph_svg, has_older=has_older,
    )


def _build_glucose_svg(points, width=700, height=220):
    if not points:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"></svg>'
    pad_left, pad_right, pad_top, pad_bottom = 50, 20, 20, 40
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    values = [v for _, v in points]
    vmin = max(0, min(values) - 1)
    vmax = max(values) + 1
    if vmax - vmin < 2:
        vmax = vmin + 2
    n = len(points)
    t0, t1 = points[0][0], points[-1][0]
    span = (t1 - t0).total_seconds() or 1
    coords = []
    for dt, v in points:
        x = pad_left + plot_w * ((dt - t0).total_seconds() / span)
        y = pad_top + plot_h * (1 - (v - vmin) / (vmax - vmin))
        coords.append((x, y, v, dt))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in coords)
    grid, dots, x_labels = [], [], []
    for i in range(5):
        yy = pad_top + plot_h * i / 4
        val = vmax - (vmax - vmin) * i / 4
        grid.append(f'<line x1="{pad_left}" y1="{yy:.1f}" x2="{width-pad_right}" y2="{yy:.1f}" stroke="#e3e8f0" stroke-width="1"/>')
        grid.append(f'<text x="{pad_left-8}" y="{yy+4:.1f}" font-size="11" text-anchor="end" fill="#5b6478">{val:.1f}</text>')
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#21b573"/>'
                   for x, y, _, _ in coords)
    step = max(1, n // 5)
    for i in range(0, n, step):
        x, _, _, dt = coords[i]
        x_labels.append(f'<text x="{x:.1f}" y="{height-10}" font-size="11" text-anchor="middle" fill="#5b6478">{dt.strftime("%d %b")}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" width="100%" height="auto" '
        f'style="background:#fff;border-radius:8px;">'
        + "".join(grid)
        + f'<polyline fill="none" stroke="#1f4d2b" stroke-width="2" points="{polyline}"/>'
        + dots + "".join(x_labels) + '</svg>'
    )


# =========================================================
# EMERGENCY / PRIVACY / LEARN
# =========================================================
@app.route("/emergency", methods=["GET", "POST"])
@login_required
def emergency():
    contact = EmergencyContact.query.filter_by(user_id=session["user_id"]).first()
    if not contact:
        contact = EmergencyContact(user_id=session["user_id"],
                                   contact_name="Not set", contact_phone="Not set")
        db.session.add(contact)
        db.session.commit()
    if request.method == "POST":
        contact.contact_name = request.form["contact_name"].strip()
        contact.contact_phone = request.form["contact_phone"].strip()
        contact.doctor_name = request.form.get("doctor_name", "").strip() or None
        contact.doctor_phone = request.form.get("doctor_phone", "").strip() or None
        contact.allergies = request.form.get("allergies", "").strip() or None
        contact.rescue_protocol = request.form.get("rescue_protocol", "").strip() or None
        db.session.commit()
        flash("Emergency info updated.", "success")
        return redirect(url_for("emergency"))
    return render_template("emergency.html", contact=contact)


@app.route("/privacy", methods=["GET", "POST"])
@login_required
def privacy():
    p = PrivacySetting.query.filter_by(user_id=session["user_id"]).first()
    if not p:
        p = PrivacySetting(user_id=session["user_id"])
        db.session.add(p)
        db.session.commit()
    if request.method == "POST":
        p.caregiver_access = bool(request.form.get("caregiver_access"))
        p.clinician_sync = bool(request.form.get("clinician_sync"))
        p.share_emergency = bool(request.form.get("share_emergency"))
        db.session.commit()
        flash("Privacy preferences saved.", "success")
        return redirect(url_for("privacy"))
    return render_template("privacy.html", privacy=p)


@app.route("/learn")
@login_required
def learn():
    all_badges = Badge.query.all()
    earned_ids = {ub.badge_id for ub in
                  UserBadge.query.filter_by(user_id=session["user_id"]).all()}
    return render_template("learn.html", badges=all_badges, earned=earned_ids)


# =========================================================
# GAMES HUB
# =========================================================
@app.route("/games")
@login_required
def games():
    uid = session["user_id"]
    status = {
        "detective": already_played(uid, "detective"),
        "injection": already_played(uid, "injection"),
        "quiz": already_played(uid, "quiz"),
    }
    return render_template("games.html", status=status)


@app.route("/games/detective", methods=["GET", "POST"])
@login_required
def game_detective():
    uid = session["user_id"]

    if already_played(uid, "detective"):
        return render_template("game_detective.html", done=True,
                               score=None, total=3,
                               scenarios=[], answers={})

    pool = DetectiveScenario.query.all()
    todays = daily_pick(pool, 3, salt="detective")

    if request.method == "POST":
        score = 0
        for s in todays:
            ans = request.form.get(f"q_{s.id}")
            if ans is not None and int(ans) == s.correct_index:
                score += 1
        record_completion(uid, "detective", score, len(todays))
        if score >= 1:
            award_badge(uid, "hypo_detective")
        update_streak_badges(uid)
        check_champion(uid)
        db.session.commit()
        flash(f"Detective round complete — {score}/{len(todays)} correct!", "success")
        return redirect(url_for("game_detective"))

    return render_template("game_detective.html", done=False,
                           score=None, total=len(todays),
                           scenarios=todays, answers={})


INJECTION_STEPS = [
    "Check insulin (date and appearance)",
    "Wash hands",
    "Select injection site",
    "Prepare the pen",
    "Inject",
    "Dispose of needle safely",
]


@app.route("/games/injection", methods=["GET", "POST"])
@login_required
def game_injection():
    uid = session["user_id"]

    if already_played(uid, "injection"):
        return render_template("game_injection.html", done=True,
                               steps=[], scenario=None, score=None)

    pool = InjectionScenario.query.all()
    today_scenario = daily_pick(pool, 1, salt="injection")
    today_scenario = today_scenario[0] if today_scenario else None

    if request.method == "POST":
        try:
            import json as _json
            submitted = _json.loads(request.form.get("order", "[]"))
        except Exception:
            submitted = []
        correct = (submitted == INJECTION_STEPS)
        record_completion(uid, "injection", 1 if correct else 0, 1)
        if correct:
            award_badge(uid, "injection_explorer")
        update_streak_badges(uid)
        check_champion(uid)
        db.session.commit()
        flash("Injection round complete!", "success")
        return redirect(url_for("game_injection"))

    shuffled_steps = daily_shuffle(INJECTION_STEPS, salt="injection-steps")
    return render_template("game_injection.html", done=False,
                           steps=shuffled_steps, scenario=today_scenario,
                           score=None)


@app.route("/games/quiz", methods=["GET", "POST"])
@login_required
def game_quiz():
    uid = session["user_id"]

    if already_played(uid, "quiz"):
        result = (DailyCompletion.query
                  .filter_by(user_id=uid, game_code="quiz", played_on=date.today())
                  .first())
        return render_template("game_quiz.html", done=True,
                               questions=[], score=result.score if result else 0,
                               total=result.total if result else 10)

    pool = QuizQuestion.query.all()
    todays = daily_pick(pool, 10, salt="quiz")

    if request.method == "POST":
        score = 0
        for q in todays:
            ans = request.form.get(f"q_{q.id}")
            if ans is not None and int(ans) == q.correct_index:
                score += 1
        record_completion(uid, "quiz", score, len(todays))
        if score == len(todays):
            award_badge(uid, "quiz_ace")
        total_quizzes = DailyCompletion.query.filter_by(
            user_id=uid, game_code="quiz").count()
        if total_quizzes >= 5:
            award_badge(uid, "quiz_learner")
        update_streak_badges(uid)
        check_champion(uid)
        db.session.commit()
        flash(f"Today's quiz complete — {score}/{len(todays)}!", "success")
        return redirect(url_for("game_quiz"))

    return render_template("game_quiz.html", done=False,
                           questions=todays, score=None, total=len(todays))


# =========================================================
# CAREGIVER
# =========================================================
@app.route("/caregiver")
@role_required("caregiver")
def caregiver_dashboard():
    links = CaregiverLink.query.filter_by(caregiver_id=session["user_id"]).all()
    patients = []
    for link in links:
        patient = db.session.get(User, link.patient_id)
        if patient:
            readings = (HealthReading.query
                        .filter_by(patient_id=patient.id)
                        .order_by(HealthReading.created_at.desc())
                        .limit(30).all())
            latest = readings[0] if readings else None
            patients.append({
                "patient": patient,
                "latest": latest,
                "readings": readings,
                "link": link,
            })
    return render_template("caregiver.html", patients=patients)


@app.route("/caregiver/pair", methods=["POST"])
@role_required("caregiver")
def caregiver_pair():
    code = request.form.get("code", "").strip()
    if not (code.isdigit() and len(code) == 4):
        flash("Please enter a 4-digit patient code.", "danger")
        return redirect(url_for("caregiver_dashboard"))
    patient = User.query.filter_by(patient_code=code, role="patient").first()
    if not patient:
        flash("No patient found with that code.", "danger")
        return redirect(url_for("caregiver_dashboard"))
    if not CaregiverLink.query.filter_by(
            caregiver_id=session["user_id"], patient_id=patient.id).first():
        db.session.add(CaregiverLink(caregiver_id=session["user_id"],
                                     patient_id=patient.id))
        db.session.commit()
    flash(f"Linked to {patient.name}.", "success")
    return redirect(url_for("caregiver_dashboard"))


# =========================================================
# INVENTORY + REFILL
# =========================================================
@app.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        item_id = request.form.get("item_id")
        if item_id:
            item = db.session.get(InventoryItem, int(item_id))
            if item and item.patient_id == session["user_id"]:
                item.quantity = int(request.form.get("quantity", item.quantity))
                item.low_threshold = int(request.form.get("low_threshold", item.low_threshold))
                db.session.commit()
                flash("Inventory updated.", "success")
        else:
            db.session.add(InventoryItem(
                patient_id=session["user_id"],
                category=request.form["category"],
                quantity=int(request.form.get("quantity", 0)),
                low_threshold=int(request.form.get("low_threshold", 5)),
            ))
            db.session.commit()
            flash("Item added.", "success")
        return redirect(url_for("inventory"))
    items = InventoryItem.query.filter_by(patient_id=session["user_id"]).all()
    return render_template("inventory.html", items=items)


@app.route("/inventory/refill")
@login_required
def inventory_refill():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    items = InventoryItem.query.filter_by(patient_id=session["user_id"]).all()
    return render_template("refill.html", items=items)


# =========================================================
# ASK (session chat: accumulates during visit, resets on return)
# =========================================================
ASK_RULES = [
    (["thank", "thanks"],
     "You're welcome{name_exclaim} If you have any more questions about managing your insulin therapy, just ask."),
    (["bye", "goodbye", "see you"],
     "Goodbye{name_comma} Remember to check your glucose regularly and stay on top of your insulin schedule. Take care!"),
    (["who are you", "what are you", "your name"],
     "I'm ASK, the 2ITSB digital assistant. I provide general guidance on insulin therapy and diabetes self-care. I'm not a substitute for your clinician."),
    (["help", "what can you do"],
     "I can help with:\n• Hypo and hyperglycaemia symptoms\n• Injection technique and site rotation\n• Insulin storage and travel\n• Missed doses and sick-day rules\n• General diabetes self-care\n\nWhat would you like to know{name_q}"),
    (["dizzy", "shaky", "shakiness"],
     "Feeling dizzy or shaky can be a sign of low blood glucose (hypoglycaemia). Check your glucose immediately. If it's below 4.0 mmol/L, take 15g of fast-acting carbohydrate and recheck in 15 minutes.{name_close}"),
    (["sweat", "sweating", "hunger", "craving", "cravings"],
     "Sweating, sudden hunger, and shakiness are common warning signs of low blood sugar. Please check your glucose now. If below 4.0 mmol/L, take 15g of fast-acting carbs and recheck in 15 minutes.{name_close}"),
    (["high blood sugar", "hyperglycemia", "hyperglycaemia", "very high"],
     "High blood glucose (above 7.0 before meals or 8.5 after meals) may be due to missed doses, illness, or diet. Drink water, avoid sugary food, and recheck in 2 hours.{name_close}"),
    (["low blood sugar", "hypoglycemia", "hypoglycaemia"],
     "Low blood glucose (below 4.0 mmol/L) needs fast treatment: 15g of fast-acting carbohydrate, wait 15 minutes, recheck. If symptoms persist, seek medical help.{name_close}"),
    (["rotate", "rotation", "site"],
     "Rotate injection sites in a pattern: abdomen (4 quadrants), thighs, arms, buttocks. Avoid the same spot twice in a row.{name_close}"),
    (["missed", "forgot", "forget"],
     "If you missed your dose, do NOT double up.{name_comma} Contact your clinician for guidance."),
    (["store", "storage", "fridge", "refrigerat"],
     "Unopened insulin pens should be refrigerated at 2-8°C. Once in use, keep at room temperature up to 28 days. Never freeze insulin."),
    (["travel", "holiday", "flight", "airport"],
     "Keep insulin in your carry-on. Carry a doctor's letter for security. Pack double supplies and fast-acting carbs."),
    (["exercise", "workout", "run", "walk"],
     "Exercise lowers blood glucose. Check before exercise; eat 15-30g of carbs if below 5.6 mmol/L."),
    (["sick", "sick day", "flu", "fever", "cold"],
     "On sick days never stop insulin. Check glucose every 2-4 hours and hydrate."),
    (["dispose", "disposal", "sharps", "bin", "throw"],
     "Used needles go into a sharps container. Return to a pharmacy when 3/4 full."),
    (["emergency", "999", "ambulance", "unconscious"],
     "If the patient is unconscious, having a seizure, or cannot be woken, call 999 immediately. Do not give food or drink."),
]


def _first_name(user):
    if not user:
        return None
    return user.name.strip().split()[0] if user.name else None


def _format_reply(template, user):
    name = _first_name(user)
    return (template
            .replace("{name_exclaim}", f", {name}!" if name else "!")
            .replace("{name_comma}", f", {name}." if name else ".")
            .replace("{name_q}", f", {name}?" if name else "?")
            .replace("{name_close}", f" Take care, {name}." if name else "")
            )


def ask_rule_match(text, user=None):
    low = text.lower().strip()
    for keywords, template in ASK_RULES:
        for k in keywords:
            if len(k) <= 4:
                if re.search(r"\b" + re.escape(k) + r"\b", low):
                    return _format_reply(template, user)
            else:
                if k in low:
                    return _format_reply(template, user)
    return None


def is_greeting(text):
    low = text.lower().strip()
    greetings = ["hello", "hi", "hey", "good morning", "good afternoon",
                 "good evening", "howdy", "yo", "greetings"]
    for g in greetings:
        if len(g) <= 4:
            if re.search(r"\b" + re.escape(g) + r"\b", low):
                return True
        else:
            if g in low:
                return True
    return False


def greeting_reply(user):
    name = _first_name(user)
    if name:
        return (f"Hello, {name}! I'm ASK, your 2ITSB assistant. "
                f"How can I help you today?\n\n"
                f"I can help with symptoms, injection technique, insulin storage, "
                f"missed doses, or general diabetes self-care.")
    return ("Hello! I'm ASK, your 2ITSB assistant. "
            "How can I help you today?\n\n"
            "I can help with symptoms, injection technique, insulin storage, "
            "missed doses, or general diabetes self-care.")


def ask_groq(user_message, user=None):
    if not GROQ_API_KEY:
        return None
    try:
        name = _first_name(user)
        name_line = f"The user's first name is {name}. Address them by name naturally." if name else ""
        system_prompt = (
            "You are ASK, a helpful and cautious assistant for patients using insulin therapy. "
            "Provide short, clear, medically-safe guidance about diabetes self-care. "
            "Always advise consulting a clinician. If the user describes an emergency, tell them to call 999. "
            f"{name_line} Keep replies under 90 words."
        )
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3, "max_tokens": 250,
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=8)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    return None


def severity_triage(text):
    kw = ["unconscious", "seizure", "can't breathe", "chest pain", "collapsed"]
    return any(k in text.lower() for k in kw)


@app.route("/ask", methods=["GET", "POST"])
def ask():
    """Session chat: accumulates during visit, resets when the user returns later."""
    user = db.session.get(User, session["user_id"]) if "user_id" in session else None

    if request.method == "POST":
        text = request.form.get("query", "").strip()
        if text:
            if is_greeting(text):
                reply = greeting_reply(user)
            elif severity_triage(text):
                reply = "[EMERGENCY] Call 999 or your emergency contact immediately."
            else:
                reply = ask_rule_match(text, user) or ask_groq(text, user) or \
                    ("I'm not sure about that one"
                     + (f", {_first_name(user)}" if user else "")
                     + ". Please consult your clinician.")

            chat = session.get("ask_chat", [])
            chat.append({"sender": "user", "content": text})
            chat.append({"sender": "ask", "content": reply})
            session["ask_chat"] = chat
            session.modified = True

        return redirect(url_for("ask"))

    referer = request.headers.get("Referer", "")
    came_from_ask = "/ask" in referer

    if not came_from_ask:
        session.pop("ask_chat", None)
        session.modified = True

    chat = session.get("ask_chat", [])

    class _M:
        def __init__(self, s, c): self.sender, self.content = s, c
    history = [_M(m["sender"], m["content"]) for m in chat]

    welcome = None
    if not history:
        welcome = greeting_reply(user)

    return render_template("ask.html", history=history,
                           groq_enabled=bool(GROQ_API_KEY),
                           welcome=welcome)


# =========================================================
# MESSAGES
# =========================================================
@app.route("/messages", methods=["GET", "POST"])
@login_required
def messages():
    user_id = session["user_id"]
    if request.method == "POST":
        receiver_id = int(request.form["receiver_id"])
        text = request.form["message"].strip()
        if text:
            db.session.add(Message(sender_id=user_id, receiver_id=receiver_id, message=text))
            db.session.commit()
            flash("Message sent.", "success")
    if session.get("role") == "patient":
        contacts = User.query.filter(User.role.in_(["doctor", "nurse"])).all()
    else:
        contacts = User.query.filter_by(role="patient").all()
    inbox = (Message.query.filter_by(receiver_id=user_id)
             .order_by(Message.created_at.desc()).all())
    return render_template("messages.html", clinicians=contacts, inbox=inbox)


# =========================================================
# SEED
# =========================================================
DETECTIVE_SEED = [
    ("John injected his usual insulin, then went for a walk. He now feels shaky, sweaty, and dizzy. What should he do first?",
     "Take another insulin dose", "Go to sleep", "Check his blood glucose immediately", "Go for another walk",
     2,
     "Correct! Shakiness, sweating, and dizziness after insulin are classic signs of hypoglycaemia. Check glucose first.",
     "Not quite. These symptoms suggest hypoglycaemia — the safest first step is to check blood glucose.",
     "hypoglycaemia"),
    ("Maria wakes up confused and slurring her speech. Her glucose is 2.8 mmol/L. She is still conscious. What do you do?",
     "Give her water", "Give her 15g of fast-acting carbs", "Give her more insulin", "Let her sleep it off",
     1,
     "Correct! 15g of fast-acting carbohydrate. Recheck in 15 minutes.",
     "Incorrect. Confusion + low glucose = treat now with fast-acting carbs.",
     "hypoglycaemia"),
    ("David's glucose is 15.2 mmol/L. He is thirsty and urinating often. What should he do first?",
     "Inject extra insulin without advice", "Drink water and check ketones if he has a meter", "Skip his next meal", "Go for a run",
     1,
     "Correct! High glucose needs water and ketone monitoring. Do NOT take extra insulin on your own.",
     "Incorrect. Taking extra insulin on your own is dangerous. Hydrate and monitor first.",
     "hyperglycaemia"),
    ("Sarah forgot her morning insulin dose. It's now lunchtime. What should she do?",
     "Double the lunch dose", "Skip lunch", "Contact her clinician for guidance", "Take the morning dose now plus lunch dose",
     2,
     "Correct! Never double up. Contact the clinician to resume safely.",
     "Incorrect. Doubling insulin can cause severe hypoglycaemia.",
     "missed-dose"),
    ("A patient's injection site feels hard and lumpy. What should they do?",
     "Inject into the lump — it absorbs faster", "Avoid that site and rotate to a fresh one", "Massage the lump and inject", "Stop insulin entirely",
     1,
     "Correct! Lumpy sites (lipohypertrophy) absorb insulin unpredictably. Always rotate.",
     "Incorrect. Never inject into a lump.",
     "site-rotation"),
    ("An insulin pen was left in a hot car for hours. What should the patient do?",
     "Use it — insulin is heat-stable", "Discard it and use a fresh pen", "Cool it in the fridge and reuse", "Use it but inject double",
     1,
     "Correct! Heat-damaged insulin loses potency. Discard and start fresh.",
     "Incorrect. Heat degrades insulin. Do not use it.",
     "storage"),
    ("Anna's glucose is 3.2 mmol/L but she feels completely fine. What should she do?",
     "Nothing — she feels fine", "Treat it as hypoglycaemia with 15g of carbs", "Take insulin", "Wait until she feels symptoms",
     1,
     "Correct! Treat any reading below 4.0 mmol/L with 15g carbs, even without symptoms.",
     "Incorrect. A reading below 4.0 needs treatment regardless of how she feels.",
     "hypoglycaemia"),
    ("Mark is vomiting and cannot keep fluids down. He has type 1 diabetes. What should he do?",
     "Wait until tomorrow", "Go to A&E / hospital now", "Stop insulin", "Drink more water and rest",
     1,
     "Correct! Persistent vomiting + diabetes = risk of DKA. Seek emergency care.",
     "Incorrect. Vomiting in a person with diabetes is a sick-day emergency.",
     "sick-day"),
    ("While packing for a flight, you notice your insulin is in the checked luggage. What should you do?",
     "Leave it — checked bags are fine", "Move it to your carry-on", "Put it in the hotel fridge when you arrive", "Wrap it in a towel inside the checked bag",
     1,
     "Correct! Checked luggage can freeze, ruining insulin. Always carry-on.",
     "Incorrect. Baggage holds can drop below freezing.",
     "travel"),
    ("You see a bruise at the patient's usual injection site. What should they do?",
     "Inject into the bruise — it's fine", "Skip insulin today", "Rotate to a different site", "Inject into the bruise but with less insulin",
     2,
     "Correct! Rotate away from bruised areas.",
     "Incorrect. Bruised sites should be avoided.",
     "site-rotation"),
    ("A patient's glucose is 5.8 mmol/L at bedtime. What should they do?",
     "Take extra insulin", "Follow their care plan — a small snack may be recommended", "Skip breakfast tomorrow", "Nothing",
     1,
     "Correct! Bedtime targets are usually 6-8 mmol/L. Follow the personalised plan.",
     "Incorrect. Follow your clinician's bedtime plan.",
     "bedtime"),
    ("A patient has fruity-smelling breath, nausea, and abdominal pain. What is the most likely cause?",
     "Hunger", "Diabetic ketoacidosis (DKA) — seek emergency care", "Mild dehydration", "A cold",
     1,
     "Correct! Fruity breath + nausea + abdominal pain = suspected DKA.",
     "Incorrect. These are classic DKA signs.",
     "emergency"),
    ("A friend finds a diabetic patient unconscious. What should they do?",
     "Give them juice", "Call 999 immediately — do not give food or drink", "Put sugar under their tongue", "Wait for them to wake up",
     1,
     "Correct! Call 999. Nothing by mouth if unconscious.",
     "Incorrect. Never give food/drink to an unconscious person.",
     "emergency"),
    ("A normally clear insulin looks cloudy. What should the patient do?",
     "Shake it and use it", "Discard it and use a fresh pen", "Warm it up — it will clear", "Use it but only half the dose",
     1,
     "Correct! Cloudy insulin may be degraded. Discard it.",
     "Incorrect. Do not use cloudy insulin that should be clear.",
     "storage"),
    ("A patient plans to exercise in an hour. Their glucose is 5.0 mmol/L. What should they do?",
     "Exercise immediately — they'll be fine", "Eat 15-30g of carbohydrates first", "Inject extra insulin", "Skip exercise",
     1,
     "Correct! Eat carbs before exercise if glucose is below 5.6 mmol/L.",
     "Incorrect. Exercising at 5.0 risks hypoglycaemia.",
     "exercise"),
    ("A patient with diabetes has a fever and their glucose is rising. What should they do?",
     "Stop insulin until the fever passes", "Keep taking insulin, monitor glucose every 2-4 hours, and hydrate", "Take paracetamol and ignore glucose", "Skip meals until better",
     1,
     "Correct! Never stop insulin when sick.",
     "Incorrect. Stopping insulin during illness is dangerous.",
     "sick-day"),
    ("A patient is going to a party where alcohol will be served. What's the best advice?",
     "Drink on an empty stomach", "Eat carbs, check glucose before bed, and inform a friend", "Skip insulin that evening", "Avoid all food and just drink water",
     1,
     "Correct! Alcohol can cause delayed hypoglycaemia.",
     "Incorrect. Never drink on an empty stomach.",
     "alcohol"),
    ("After injecting, the patient notices a drop of blood. What should they do?",
     "Massage the site", "Apply light pressure, do not massage", "Inject again in the same spot", "Take extra insulin",
     1,
     "Correct! Light pressure only.",
     "Incorrect. Do not massage.",
     "technique"),
    ("A patient asks about reusing needles to save money. What should you advise?",
     "It's fine for a few uses", "Never reuse — each injection needs a fresh needle", "Reuse only if you clean it", "Reuse only for basal doses",
     1,
     "Correct! Reusing needles causes pain, infection, and lipohypertrophy.",
     "Incorrect. Needles are single-use.",
     "disposal"),
    ("You're travelling overseas. How should insulin be stored?",
     "In the hotel fridge only", "In carry-on with a cool pack, then in the hotel fridge", "In the sun to keep it warm", "In the freezer",
     1,
     "Correct! Carry-on + cool pack + hotel fridge. Never freeze.",
     "Incorrect. Freezing damages insulin.",
     "travel"),
    ("A patient has very high glucose and severe abdominal pain. What should they do?",
     "Wait a few hours", "Call emergency services or go to A&E", "Take extra insulin", "Drink sugary drinks",
     1,
     "Correct! Severe abdominal pain + very high glucose may mean DKA.",
     "Incorrect. This is a medical emergency.",
     "emergency"),
    ("The sharps container is about 3/4 full. What should the patient do?",
     "Keep using it until completely full", "Seal it and take it to a pharmacy or collection point", "Put it in household waste", "Empty it into a plastic bag",
     1,
     "Correct! Seal at 3/4 and dispose safely.",
     "Incorrect. Overfilling risks needle-stick injuries.",
     "disposal"),
    ("A patient is about to inject into a scarred area. What should they do?",
     "Inject anyway", "Choose a different site", "Inject at half dose", "Use a longer needle",
     1,
     "Correct! Scarred tissue absorbs insulin unevenly.",
     "Incorrect. Avoid scars.",
     "site-rotation"),
    ("Before testing blood glucose, what should the patient do?",
     "Nothing special", "Wash hands with soap and water", "Use a new lancet only if hands are dirty", "Wipe the finger with alcohol only",
     1,
     "Correct! Clean hands prevent false readings and infection.",
     "Incorrect. Always wash hands first.",
     "technique"),
    ("A patient feels unusually hungry and irritable. What should they do?",
     "Eat immediately without testing", "Check blood glucose", "Skip the next meal", "Take insulin",
     1,
     "Correct! Hunger and irritability can signal low glucose.",
     "Incorrect. Test first.",
     "hypoglycaemia"),
    ("A glucose reading looks wrong compared to how the patient feels. What should they do?",
     "Act on the reading alone", "Retest before acting", "Take insulin based on the reading", "Ignore both",
     1,
     "Correct! Always retest if a reading doesn't match symptoms.",
     "Incorrect. Retest to confirm before making decisions.",
     "technique"),
    ("A patient is unsure which insulin to use. What should they do?",
     "Use whichever is closest", "Check labels carefully and contact the clinician if unsure", "Use the newer one", "Skip the dose",
     1,
     "Correct! Never guess which insulin to use.",
     "Incorrect. Guessing can cause serious harm.",
     "safety"),
    ("A patient's vision suddenly becomes blurry. What should they do?",
     "Ignore it", "Check glucose and see a clinician if it persists", "Buy glasses immediately", "Stop insulin",
     1,
     "Correct! Blurred vision can be from very high/low glucose or retinopathy.",
     "Incorrect. Do not ignore vision changes.",
     "complications"),
    ("A patient wants to drive but their glucose is 3.5 mmol/L. What should they do?",
     "Drive carefully", "Treat with 15g of fast-acting carbs, wait 15 minutes, retest, then drive if safe", "Drink coffee and drive", "Take insulin and drive",
     1,
     "Correct! Never drive below 4.0 mmol/L.",
     "Incorrect. Driving with low glucose is dangerous.",
     "driving"),
    ("A patient reports persistent numbness in their feet. What should they do?",
     "Ignore it — it's normal", "Report it to their clinician", "Massage their feet daily", "Take vitamin supplements only",
     1,
     "Correct! Persistent numbness may indicate neuropathy.",
     "Incorrect. Numbness can be a sign of nerve damage.",
     "complications"),
]


INJECTION_CONTEXTS_SEED = [
    ("Standard morning injection", "It's 8:00 AM. You're about to take your usual basal insulin. What's the correct order of steps?"),
    ("Pen just out of the fridge", "Your insulin pen was in the fridge. You've let it warm up. Time to inject."),
    ("Injection during travel", "You're at an airport hotel. You need to inject before breakfast. Stay organised."),
    ("Injecting for a child", "You're helping a child with their injection. Use a shorter needle and pinch the skin gently."),
    ("Post-workout injection", "You've just finished a 30-minute walk. Check your glucose, then prepare your injection."),
    ("Before a big meeting", "You need to inject before a work presentation. Focus on doing each step properly."),
    ("Evening basal dose", "It's 10:00 PM. Time for your bedtime basal insulin."),
    ("After a long day", "You're tired but must not skip your injection. Slow down and do each step carefully."),
    ("Family helping out", "A relative is watching you inject for the first time. Talk them through each step."),
    ("New pen cartridge", "You've just changed the insulin cartridge. The pen must be primed before use."),
    ("At a restaurant", "You're at a restaurant bathroom, discreetly injecting before your meal."),
    ("Camping trip", "You're on a camping trip. Keep your equipment clean even outdoors."),
    ("Hot day", "It's 34°C outside. You're injecting indoors, but the heat demands extra care with storage."),
    ("Cold morning", "It's 5°C outside. Your insulin pen may feel cold — let it warm up before injecting."),
    ("Morning rush", "You're running late. Do NOT skip steps — do them in the right order quickly."),
    ("After swimming", "You've just dried off after a swim. Time for your scheduled injection."),
    ("At grandmother's house", "You're at a family gathering. Excuse yourself to inject quietly and correctly."),
    ("Before a flight", "You're at the airport lounge. Inject before boarding in case meals are delayed."),
    ("First-time user", "You've just been taught how to inject. Walk through each step slowly."),
    ("Night shift worker", "You work nights. Your injection schedule shifted. Do the steps correctly."),
    ("Ramadan fasting", "You're fasting. Your clinician adjusted your schedule. Follow the new plan."),
    ("After a hypo", "You've just recovered from low glucose. It's time for your next scheduled dose."),
    ("Injection for a toddler", "The toddler is fussy. Stay calm, use the correct technique."),
    ("Sports day", "You're a coach with type 1 diabetes. You need to inject before your own session."),
    ("Long car journey", "You're on a road trip. Pull over somewhere clean before injecting."),
    ("Power outage", "The power is out at home. Follow the same steps in dim light carefully."),
    ("Wedding day", "You're at a wedding. Find a quiet room for your injection."),
    ("Hotel room", "You're in a hotel. Familiar surroundings don't mean skipping steps."),
    ("Back to school", "A student needs an injection. Follow the school's protocol and steps."),
    ("After illness", "You've just recovered from the flu. Resume your routine carefully."),
    ("Emergency backup pen", "Your main pen is empty. Use your backup pen — check it first."),
    ("Routine check", "Just a normal day. Take your usual injection, but don't rush."),
]


QUIZ_QUESTIONS_SEED = [
    ("When should rapid-acting insulin typically be injected?",
     "3 hours after a meal", "10–15 minutes before a meal", "Only at bedtime", "When glucose is above 10",
     1, "insulin-timing"),
    ("What is lipohypertrophy?",
     "Low blood sugar overnight", "Fatty lumps from repeated injections at the same site", "A type of insulin brand", "An allergic reaction",
     1, "site-rotation"),
    ("Correct pinch angle for a subcutaneous injection?",
     "10 degrees", "45 or 90 degrees", "180 degrees", "0 degrees",
     1, "technique"),
    ("Normal blood glucose before meals (mmol/L)?",
     "1.0–2.5", "4.4–7.0", "10–12", "15–18",
     1, "glucose-range"),
    ("Normal blood glucose after meals (mmol/L)?",
     "Under 4.4", "Under 8.5", "Over 12", "Over 15",
     1, "glucose-range"),
    ("A glucose reading below ____ mmol/L is hypoglycaemia.",
     "3.0", "4.0", "6.0", "8.0",
     1, "hypoglycaemia"),
    ("Best treatment for mild hypoglycaemia?",
     "15g of fast-acting carbohydrate", "More insulin", "Sleep", "Exercise",
     0, "hypoglycaemia"),
    ("After treating a hypo, how long before rechecking glucose?",
     "5 minutes", "15 minutes", "1 hour", "4 hours",
     1, "hypoglycaemia"),
    ("Where should unopened insulin be stored?",
     "Freezer", "Fridge at 2–8°C", "Direct sunlight", "Hot car",
     1, "storage"),
    ("Once in use, how long can an insulin pen typically be kept at room temperature?",
     "24 hours", "7 days", "28 days", "1 year",
     2, "storage"),
    ("What should you do if insulin freezes?",
     "Use it normally", "Discard it", "Warm it up and use", "Inject double",
     1, "storage"),
    ("Clear insulin that becomes cloudy means:",
     "It's still fine", "It should be discarded", "It's stronger now", "It's for a different patient",
     1, "storage"),
    ("Best place to keep insulin while flying?",
     "Checked luggage", "Carry-on with a cool pack", "In the overhead bin without cooling", "In a checked bag with ice",
     1, "travel"),
    ("Why is site rotation important?",
     "It's optional", "Prevents lipohypertrophy and improves absorption", "Only for children", "Only for long needles",
     1, "site-rotation"),
    ("How many times can a pen needle be reused?",
     "Once only", "3 times", "10 times", "Until it's blunt",
     0, "disposal"),
    ("Where should used needles go?",
     "Household bin", "Recycling bin", "Sharps container", "Flush down the toilet",
     2, "disposal"),
    ("Best time to check for ketones?",
     "Never", "When glucose is above 14 mmol/L or feeling unwell", "Only after exercise", "Once a year",
     1, "ketones"),
    ("Symptoms of hypoglycaemia include:",
     "Thirst and frequent urination", "Shakiness, sweating, dizziness", "Blurred vision only", "Weight gain",
     1, "hypoglycaemia"),
    ("Symptoms of hyperglycaemia include:",
     "Thirst, frequent urination, fatigue", "Cold and clammy skin", "Sudden hunger", "Sweating",
     0, "hyperglycaemia"),
    ("Best treatment for DKA?",
     "More insulin at home", "Emergency medical care", "Water and rest", "Extra food",
     1, "emergency"),
    ("Bedtime glucose target for most adults is:",
     "3–4 mmol/L", "6–8 mmol/L", "10–12 mmol/L", "14–16 mmol/L",
     1, "bedtime"),
    ("What should you do if glucose is 3.5 mmol/L and you're about to drive?",
     "Drive carefully", "Treat with 15g carbs, wait 15 min, retest, then drive", "Drink coffee and drive", "Take insulin",
     1, "driving"),
    ("What is 'hypo unawareness'?",
     "Not feeling hypo symptoms when glucose is low", "Fear of needles", "Not knowing your insulin dose", "A type of insulin",
     0, "hypoglycaemia"),
    ("Exercise usually ____ blood glucose.",
     "Raises", "Lowers", "Doesn't affect", "Doubles",
     1, "exercise"),
    ("Best approach on sick days?",
     "Stop insulin", "Keep insulin, monitor glucose, hydrate", "Skip meals until better", "Take double insulin",
     1, "sick-day"),
    ("Alcohol can cause ____ hypoglycaemia.",
     "Immediate", "Delayed", "Only morning", "None",
     1, "alcohol"),
    ("Before injecting, you should always:",
     "Inject straight away", "Wash hands and check insulin", "Reuse the needle", "Skip the site check",
     1, "technique"),
    ("Numbness or tingling in feet may suggest:",
     "Nothing serious", "Neuropathy — report to clinician", "Improved circulation", "Vitamin C deficiency",
     1, "complications"),
    ("What is the 'rule of 15' for hypoglycaemia?",
     "15g of carbs, wait 15 min, recheck", "15 minutes of exercise", "15 units of insulin", "15 minutes of sleep",
     0, "hypoglycaemia"),
    ("Basal insulin's main role is to:",
     "Cover meals", "Provide background insulin throughout the day", "Replace exercise", "Treat hypos",
     1, "insulin-types"),
    ("Bolus insulin's main role is to:",
     "Cover meals", "Provide background insulin", "Replace basal", "Treat lows",
     0, "insulin-types"),
    ("Best injection sites for insulin?",
     "Arms only", "Abdomen, thighs, arms, buttocks", "Anywhere on the body", "Only the abdomen",
     1, "site-rotation"),
    ("If insulin is exposed to extreme heat:",
     "It works faster", "Its potency drops — discard it", "It becomes long-acting", "It works normally",
     1, "storage"),
    ("Before injecting, hands should be:",
     "Wet", "Clean and dry", "Wearing gloves only", "Not important",
     1, "technique"),
    ("Best bedtime snack for someone at risk of overnight hypos?",
     "Nothing", "A small slow-acting carb snack if advised", "Candy bar only", "Energy drink",
     1, "bedtime"),
    ("Low glucose + confusion + sweating means:",
     "High glucose", "Hypoglycaemia — treat now", "DKA", "Just stress",
     1, "hypoglycaemia"),
    ("Fruity breath, nausea, and abdominal pain suggest:",
     "Hypoglycaemia", "DKA — emergency", "Flu", "Dehydration only",
     1, "emergency"),
    ("Which is NOT a hypo symptom?",
     "Shakiness", "Sweating", "Excessive thirst", "Dizziness",
     2, "hypoglycaemia"),
    ("How often should a sharps container be replaced?",
     "Never", "When 3/4 full", "Once a year", "Only when broken",
     1, "disposal"),
    ("Driving with glucose below 4.0 mmol/L:",
     "Is fine", "Is dangerous — treat first", "Is required", "Speeds up absorption",
     1, "driving"),
]


def seed_content():
    if not Badge.query.first():
        db.session.add_all([
            Badge(code="hypo_detective", label="Hypoglycaemia Detective",
                  description="Complete Emergency Detective correctly", icon="🕵️"),
            Badge(code="injection_explorer", label="Injection Explorer",
                  description="Complete Build Your Injection correctly", icon="🎯"),
            Badge(code="quiz_ace", label="Quiz Ace",
                  description="Score 100% on a daily quiz", icon="🧠"),
            Badge(code="quiz_learner", label="Quiz Learner",
                  description="Complete 5 daily quizzes", icon="📚"),
            Badge(code="streak_7", label="7-Day Streak",
                  description="Log 7 health readings in 7 days", icon="🔥"),
            Badge(code="rotation_master", label="Site Rotation Master",
                  description="Log 4+ different injection sites", icon="💉"),
            Badge(code="streak_game_3", label="3-Day Player",
                  description="Play any game 3 days in a row", icon="📅"),
            Badge(code="streak_game_7", label="7-Day Player",
                  description="Play any game 7 days in a row", icon="📅"),
            Badge(code="streak_game_30", label="30-Day Legend",
                  description="Play any game 30 days in a row", icon="📅"),
            Badge(code="insulin_champion", label="Insulin Safety Champion",
                  description="Earn all other badges", icon="🏅"),
        ])
        db.session.commit()

    if not DetectiveScenario.query.first():
        for row in DETECTIVE_SEED:
            db.session.add(DetectiveScenario(
                situation=row[0], choice_a=row[1], choice_b=row[2],
                choice_c=row[3], choice_d=row[4], correct_index=row[5],
                feedback_correct=row[6], feedback_wrong=row[7], category=row[8]))
        db.session.commit()

    if not InjectionScenario.query.first():
        for title, blurb in INJECTION_CONTEXTS_SEED:
            db.session.add(InjectionScenario(context_title=title, context_blurb=blurb))
        db.session.commit()

    if not QuizQuestion.query.first():
        for row in QUIZ_QUESTIONS_SEED:
            db.session.add(QuizQuestion(
                question=row[0], choice_a=row[1], choice_b=row[2],
                choice_c=row[3], choice_d=row[4], correct_index=row[5],
                topic=row[6]))
        db.session.commit()


def seed_demo_accounts():
    if User.query.count() > 0:
        return

    demo = [
        ("Demo Patient", "patient@2itsb.demo", "patient", "P-001"),
        ("Demo Caregiver", "caregiver@2itsb.demo", "caregiver", "C-001"),
        ("Demo Nurse", "nurse@2itsb.demo", "nurse", "N-001"),
        ("Demo Doctor", "doctor@2itsb.demo", "doctor", "D-001"),
    ]
    users = {}
    for name, email, role, ident in demo:
        u = User(
            name=name, email=email, role=role,
            identification_id=ident,
            password_hash=generate_password_hash("123456"),
            patient_code="0415" if role == "patient" else None,
        )
        db.session.add(u)
        users[role] = u
    db.session.commit()

    for u in users.values():
        db.session.add(PrivacySetting(user_id=u.id))
        db.session.add(EmergencyContact(
            user_id=u.id, contact_name="Next of Kin", contact_phone="+60 12-345 6789"))
    db.session.commit()

    p = users["patient"]
    demo_readings = [
        (5.5, "mmol/L", "Before Meal", 120, 80, 97, 36.0, 65.0, "Sudden sugar cravings"),
        (6.2, "mmol/L", "After Meal", 118, 78, 98, 36.4, 65.0, None),
        (5.1, "mmol/L", "Before Meal", 121, 80, 97, 36.2, 65.0, None),
    ]
    for g, u_, c, s, d, sp, t, w, sym in demo_readings:
        db.session.add(HealthReading(
            patient_id=p.id, glucose=g, glucose_unit=u_, context=c,
            systolic=s, diastolic=d, spo2=sp, temperature=t, weight=w, symptoms=sym))

    db.session.add(Medication(patient_id=p.id, name="Insulin", schedule="Morning Basal",
                              dose_units=14, time_of_day="08:00"))
    db.session.add(Medication(patient_id=p.id, name="Insulin", schedule="Lunch Bolus",
                              dose_units=8, time_of_day="13:00"))
    db.session.add(Medication(patient_id=p.id, name="Insulin", schedule="Dinner Bolus",
                              dose_units=10, time_of_day="19:00"))

    db.session.add(InventoryItem(patient_id=p.id, category="pen", quantity=1, low_threshold=1))
    db.session.add(InventoryItem(patient_id=p.id, category="needle", quantity=20, low_threshold=5))
    db.session.add(InventoryItem(patient_id=p.id, category="strip", quantity=30, low_threshold=10))
    db.session.add(InventoryItem(patient_id=p.id, category="lancet", quantity=20, low_threshold=5))

    db.session.add(ChatMessage(
        user_id=p.id, sender="user",
        content="Hi, lately I've been sweating frequently and getting sudden hunger cravings."))
    db.session.add(ChatMessage(
        user_id=p.id, sender="ask",
        content=("Sweating, sudden hunger, and shakiness can be warning signs of low blood sugar. "
                 "Please check your glucose level now. If below 4.0 mmol/L, take 15g of fast-acting "
                 "carbohydrates and recheck in 15 minutes.")))

    db.session.commit()


with app.app_context():
    db.create_all()
    seed_content()
    seed_demo_accounts()


if __name__ == "__main__":
    app.run(debug=True)