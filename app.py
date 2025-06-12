# app.py
import os
import io
import base64
import random

from flask import (
    Flask, render_template, request, redirect, url_for, session
)
from flask_sqlalchemy import SQLAlchemy

import pyotp, qrcode

# ─── Import your quantum‐random interface ─────────────────────────────
import rng    # assumed to define get_random_seed() and get_random()

# ─── CONFIGURATION ────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change_me")

# SQLite + SQLAlchemy
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///qauth.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Fallback switch: set NV_DETUNED=1 in your env to force seeds.txt & PRNG fallback
NV_DETUNED = os.environ.get("NV_DETUNED", "") in ("1", "true", "True")


# ─── DATABASE MODEL ────────────────────────────────────────────────────
class UserSecret(db.Model):
    __tablename__ = "user_secrets"
    email  = db.Column(db.String(255), primary_key=True)
    secret = db.Column(db.String(32), nullable=False)

    def __repr__(self):
        return f"<UserSecret {self.email}>"


if not os.path.exists("qauth.db"):
    with app.app_context():
        db.create_all()


# ─── HELPER: Load next seed from seeds.txt ─────────────────────────────
def pop_fallback_secret():
    """
    Reads the first line of seeds.txt (32-char Base32 secret),
    removes it from the file, and returns it.
    """
    lines = []
    with open("seeds.txt", "r") as f:
        lines = f.read().splitlines()
    if not lines:
        raise RuntimeError("seeds.txt is empty!")
    secret = lines.pop(0).strip()
    # Write back the remainder
    with open("seeds.txt", "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return secret


# ─── ROUTE: Generate a new QR & secret ────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        # ── 1) Obtain a Base32 secret ────────────────────────────────
        if not NV_DETUNED:
            # Quantum‐random: get 160 bits, pack into 20 bytes, Base32‐encode
            bits = rng.get_random_seed()   # e.g. [0,1,1,0,...] length 160
            # Pack 8 bits → one byte, repeat 20 times
            b = bytes(
                int("".join(str(bit) for bit in bits[i*8:(i+1)*8]), 2)
                for i in range(20)
            )
            secret = base64.b32encode(b).decode("utf-8").rstrip("=")
        else:
            # Fallback: pop from seeds.txt
            secret = pop_fallback_secret()

        # ── 2) Upsert into DB ───────────────────────────────────────
        user = UserSecret.query.get(email)
        if user:
            user.secret = secret
        else:
            user = UserSecret(email=email, secret=secret)
            db.session.add(user)
        db.session.commit()

        # ── 3) Build provisioning URI with issuer="QAuth" ──────────
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=email, issuer_name="QAuth"
        )

        # ── 4) Render QR code to base64 PNG ────────────────────────
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_png = base64.b64encode(buf.getvalue()).decode("utf-8")

        return render_template(
            "show_qr.html",
            secret=secret,
            qr_data=f"data:image/png;base64,{b64_png}"
        )

    return render_template("index.html")


# ─── ROUTE: Verify TOTP ────────────────────────────────────────────────
@app.route("/verify", methods=["GET", "POST"])
def verify():
    message = None
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        token = request.form["token"].strip()

        user = UserSecret.query.get(email)
        if not user:
            message = "No secret found—generate a new QR first."
        else:
            totp = pyotp.TOTP(user.secret)
            if totp.verify(token, valid_window=1):
                session.clear()
                session["email"] = email
                return redirect(url_for("lotto"))
            else:
                message = "❌ Invalid code. Check your clock."

    return render_template("verify.html", message=message)


# ─── ROUTE: Quantum Lotto ──────────────────────────────────────────────
@app.route("/lotto", methods=["GET", "POST"])
def lotto():
    if "email" not in session:
        return redirect(url_for("verify"))

    if request.method == "POST":
        guess = request.form["guess"].strip()

        # Validate exactly 4 octal digits [0-7]
        if not (len(guess) == 4 and all(ch in "01234567" for ch in guess)):
            error = "Enter exactly 4 octal digits (0–7)."
            return render_template("lotto.html", error=error)

        # ── 1) Generate the 12‐bit quantum random for the draw ────
        if not NV_DETUNED:
            bits12 = rng.get_random()    # e.g. [1,0,1, ...] length 12
            # Group into four 3‐bit chunks → digits 0–7
            drawn_number = "".join(
                str(int("".join(str(bit) for bit in bits12[i*3:(i+1)*3]), 2))
                for i in range(4)
            )
        else:
            # Fallback: deterministic PRNG seeded by this user’s secret
            user = UserSecret.query.get(session["email"])
            rnd = random.Random(user.secret)
            drawn_number = "".join(str(rnd.randint(0, 7)) for _ in range(4))

        # ── 2) Determine prize level ────────────────────────────────
        if drawn_number == guess:
            prize = "grand"
        elif drawn_number[1:] == guess[1:]:
            prize = "merch"
        else:
            prize = "none"

        return render_template(
            "result.html",
            guess=guess,
            drawn=drawn_number,
            prize=prize
        )

    return render_template("lotto.html", error=None)


# ─── ROUTE: Logout ──────────────────────────────────────────────────────
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    # You can still use `flask run --host=0.0.0.0` or `python app.py`
    app.run(debug=True)
