# app.py
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import pyotp, qrcode, io, base64, random, os

app = Flask(__name__)
# ─── IMPORTANT: set a secret key for sessions ───────────────────────────────────
app.secret_key = "replace_this_with_a_random_secret_key"  # ◀︎ Change this in production

# ─── Database config ──────────────────────────────────────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///qauth.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ─── Model: store each email → Base32 secret ───────────────────────────────────
class UserSecret(db.Model):
    __tablename__ = "user_secrets"
    email  = db.Column(db.String(255), primary_key=True)
    secret = db.Column(db.String(32), nullable=False)

    def __repr__(self):
        return f"<UserSecret {self.email}>"

# ─── Create the DB + table if it doesn’t exist ─────────────────────────────────
if not os.path.exists("qauth.db"):
    with app.app_context():
        db.create_all()

# ─── Route: “/” = Generate a new QR & secret ───────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    """
    GET  → show form to generate a new QR code (email only)
    POST → generate a new Base32 secret, upsert in DB, show QR + secret
    """
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        # 1) Generate a fresh Base32 secret
        new_secret = pyotp.random_base32()

        # 2) Upsert: if this email exists, overwrite; else insert
        user = UserSecret.query.filter_by(email=email).first()
        if user:
            user.secret = new_secret
        else:
            user = UserSecret(email=email, secret=new_secret)
            db.session.add(user)
        db.session.commit()

        # 3) Build provisioning URI with issuer="QAuth"
        uri = pyotp.TOTP(new_secret).provisioning_uri(
            name=email,
            issuer_name="QAuth"
        )

        # 4) Render the QR code into a base64 PNG
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_png = base64.b64encode(buf.getvalue()).decode("utf-8")

        # 5) Show the “show_qr.html” template with both the secret and the QR PNG
        return render_template(
            "show_qr.html",
            secret=new_secret,
            qr_data=f"data:image/png;base64,{b64_png}"
        )

    # GET → just display the email form
    return render_template("index.html")


# ─── Route: “/verify” = verify TOTP code ────────────────────────────────────────
@app.route("/verify", methods=["GET", "POST"])
def verify():
    """
    GET  → show form (email + 6-digit TOTP)
    POST → check the submitted TOTP against the stored secret.
            If valid, store the email in session and redirect to /lotto.
            Else, re-render the verify page with an error message.
    """
    message = None
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        token = request.form["token"].strip()

        user = UserSecret.query.filter_by(email=email).first()
        if not user:
            message = "No secret found for that email. Generate a new QR first."
        else:
            totp = pyotp.TOTP(user.secret)
            # valid_window=1 allows a ±30s clock skew
            if totp.verify(token, valid_window=1):
                # OTP valid → “log in” by saving to session and redirect to /lotto
                session.clear()
                session["email"] = email
                return redirect(url_for("lotto"))
            else:
                message = "❌ Invalid code. Make sure your device’s clock is correct."

    return render_template("verify.html", message=message)


# ─── Route: “/lotto” = Quantum Lotto page where user enters a 4-digit guess ────
@app.route("/lotto", methods=["GET", "POST"])
def lotto():
    """
    GET  → show a form: “Enter your 4-digit guess”
    POST → read guess (must be exactly 4 digits), generate random 4-digit draw,
            compute prize (grand / merch / none), then render “result.html”.
    """
    # Must be “logged in” (i.e. have email in session) to play
    if "email" not in session:
        return redirect(url_for("verify"))

    if request.method == "POST":
        guess = request.form["guess"].strip()
        # Validate guess is exactly 4 digits
        if not (guess.isdigit() and len(guess) == 4):
            error = "Please enter exactly 4 digits (e.g. 0123)."
            return render_template("lotto.html", error=error)

        # 1) Generate a random 4-digit string, zero‐padded
        drawn_number = f"{random.randint(0, 9999):04d}"

        # 2) Determine prize
        if drawn_number == guess:
            prize = "grand"
        elif drawn_number[1:] == guess[1:]:  # last 3 digits match
            prize = "merch"
        else:
            prize = "none"

        # 3) Render the “result.html” page, passing:
        #    - the user's guess
        #    - the drawn number
        #    - the prize code
        return render_template(
            "result.html",
            guess=guess,
            drawn=drawn_number,
            prize=prize
        )

    # GET → show the guess‐entry form
    return render_template("lotto.html", error=None)


# ─── (Optional) Route: “/logout” to clear the session ───────────────────────────
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    # If you prefer to call `python app.py` directly, uncomment below:
    # app.run(host="0.0.0.0", port=5000, debug=True)
    #
    # Otherwise, you can still use `flask run --host=0.0.0.0` from the CLI.
    app.run(debug=True)
