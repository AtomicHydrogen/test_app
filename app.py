# app.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import pyotp, qrcode, io, base64
import os

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///qauth.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ───────────────────────────────────────────────────────────
# Database model: store one row per email → secret
class UserSecret(db.Model):
    __tablename__ = "user_secrets"
    email  = db.Column(db.String(255), primary_key=True)
    secret = db.Column(db.String(32), nullable=False)

    def __repr__(self):
        return f"<UserSecret {self.email}>"

# Ensure the database and table exist, but do it inside an app context
if not os.path.exists("qauth.db"):
    with app.app_context():
        db.create_all()
# ───────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    """
    1) GET: show form to request a new QR & secret.
    2) POST: generate a new secret, store/update in DB, then render QR.
    """
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        # 1) Generate (or regenerate) secret
        new_secret = pyotp.random_base32()

        # 2) Upsert into SQLite: if email exists, overwrite the secret; else insert
        user = UserSecret.query.filter_by(email=email).first()
        if user:
            user.secret = new_secret
        else:
            user = UserSecret(email=email, secret=new_secret)
            db.session.add(user)
        db.session.commit()

        # 3) Build provisioning URI (issuer name is "QAuth")
        uri = pyotp.TOTP(new_secret).provisioning_uri(
            name=email,
            issuer_name="QAuth"
        )

        # 4) Render QR code in-memory
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

        # 5) Send base64-encoded PNG + plaintext secret to template
        return render_template("show_qr.html",
                               secret=new_secret,
                               qr_data=f"data:image/png;base64,{b64_img}")

    # GET
    return render_template("index.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    """
    1) GET: show form (email + 6-digit token).
    2) POST: look up stored secret for that email, then run TOTP.verify(token).
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
            # Allow a ± one-step window (default is window=0)
            if totp.verify(token, valid_window=1):
                message = "✅ Code is valid!"
            else:
                message = "❌ Invalid code. Make sure your device’s clock is correct."

    return render_template("verify.html", message=message)


if __name__ == "__main__":
    app.run(debug=True)
