"""AutoTailor Flask app.

MVP: passwordless sign-in via 6-digit code (printed to console), profile
management, stubbed resume generation (PDF/DOCX), and downloads.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, URL as UrlValidator

from db import SessionLocal, init_db, engine
from models import Base, GeneratedResume, Profile, User
from adapters.resume_engine import generate_both


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "dev_key")

    # Initialize DB
    init_db(Base)

    # Ensure optional columns exist on existing SQLite DBs (lightweight dev migration)
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        profile_cols = {c["name"] for c in inspector.get_columns("profiles")}
    except Exception:
        profile_cols = set()

    to_add = []
    if "about" not in profile_cols:
        to_add.append("ALTER TABLE profiles ADD COLUMN about TEXT")
    if "gemini_api_key" not in profile_cols:
        to_add.append("ALTER TABLE profiles ADD COLUMN gemini_api_key VARCHAR(255)")
    if to_add:
        with engine.begin() as conn:
            for stmt in to_add:
                conn.execute(text(stmt))
        # Reinspect after altering
        inspector = inspect(engine)
        profile_cols = {c["name"] for c in inspector.get_columns("profiles")}

    HAS_PROFILE_ABOUT = "about" in profile_cols
    HAS_PROFILE_GEMINI = "gemini_api_key" in profile_cols

    # Login manager
    login_manager = LoginManager()
    login_manager.login_view = "signin"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        sess = SessionLocal()
        return sess.get(User, int(user_id))

    # Teardown: remove scoped session
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        SessionLocal.remove()

    # Forms
    class SigninForm(FlaskForm):
        email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])

    class VerifyForm(FlaskForm):
        email = HiddenField("Email", validators=[DataRequired(), Email(), Length(max=255)])
        code = StringField("Code", validators=[DataRequired(), Length(min=6, max=6)])

    class ProfileForm(FlaskForm):
        full_name = StringField("Full name", validators=[DataRequired(), Length(max=255)])
        city = StringField("City", validators=[Length(max=255)])
        email = StringField("Email", validators=[Email(), Length(max=255)])
        phone = StringField("Phone", validators=[Length(max=64)])
        web = StringField("LinkedIn/GitHub/Website", validators=[Length(max=255)])
        about = TextAreaField("About", validators=[Length(max=5000)])
        gemini_api_key = StringField("Gemini API Key", validators=[Length(max=255)])

    class TailorForm(FlaskForm):
        url = StringField("URL", validators=[DataRequired(), Length(max=2000)])

    class DeleteResumeForm(FlaskForm):
        """Empty form with CSRF for deleting a resume."""
        pass

    # Routes
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("tailor"))
        return redirect(url_for("signin"))

    @app.route("/signin", methods=["GET", "POST"])
    def signin():
        form = SigninForm()
        if request.method == "POST" and form.validate_on_submit():
            email = form.email.data.strip().lower()
            sess = SessionLocal()
            user = sess.query(User).filter(User.email == email).one_or_none()
            if not user:
                user = User(email=email, verified=0)
                sess.add(user)
                sess.flush()
            # Generate 6-digit code
            import random

            code = f"{random.randint(0, 999999):06d}"
            user.verify_code = code
            user.verify_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
            sess.commit()
            # Dev console print only (do not log elsewhere)
            print(f"[DEV] Verification code: {code}")
            return redirect(url_for("verify", email=email))
        return render_template("signin.html", form=form)

    @app.route("/verify", methods=["GET", "POST"])
    def verify():
        email = request.args.get("email") or request.form.get("email") or ""
        form = VerifyForm(email=email)
        if request.method == "POST" and form.validate_on_submit():
            email = form.email.data.strip().lower()
            code = form.code.data.strip()
            sess = SessionLocal()
            user = sess.query(User).filter(User.email == email).one_or_none()
            now = datetime.now(timezone.utc)
            # Coerce SQLite-returned naive datetimes to UTC-aware for comparison
            expiry = user.verify_expiry if user else None
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if (
                user
                and user.verify_code == code
                and expiry is not None
                and expiry >= now
            ):
                user.verified = 1
                user.verify_code = None
                user.verify_expiry = None
                sess.commit()
                login_user(user)
                # ensure profile exists? redirect logic below handles missing
                prof = sess.query(Profile).filter(Profile.user_id == user.id).one_or_none()
                if not prof or not (prof.full_name or "").strip():
                    return redirect(url_for("profile"))
                return redirect(url_for("tailor"))
            else:
                flash("Invalid or expired code")
                return redirect(url_for("signin"))
        return render_template("verify.html", form=form)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        sess = SessionLocal()
        prof = sess.query(Profile).filter(Profile.user_id == current_user.id).one_or_none()
        # Build form with custom field mapping
        form = ProfileForm(obj=None)
        if prof:
            form.full_name.data = prof.full_name
            form.city.data = prof.city
            form.email.data = prof.email or current_user.email
            form.phone.data = prof.phone
            # Combine linkedin/github into one field for display
            form.web.data = prof.linkedin or prof.github
            if HAS_PROFILE_ABOUT:
                form.about.data = getattr(prof, "about", None)
            if HAS_PROFILE_GEMINI:
                form.gemini_api_key.data = getattr(prof, "gemini_api_key", None)
        # Prefill email from user if profile missing email
        if request.method == "GET":
            if not prof and current_user.email:
                form.email.data = current_user.email
        if request.method == "POST" and form.validate_on_submit():
            data = {
                "full_name": form.full_name.data.strip(),
                "city": (form.city.data or "").strip(),
                "email": (form.email.data or "").strip(),
                "phone": (form.phone.data or "").strip(),
                # Store combined web into linkedin; clear github
                "linkedin": (form.web.data or "").strip(),
                "github": None,
            }
            if prof is None:
                prof = Profile(user_id=current_user.id, **data)
                # Optional fields not in base dict (backward compatible schema)
                if HAS_PROFILE_ABOUT:
                    prof.about = (form.about.data or "").strip()
                if HAS_PROFILE_GEMINI:
                    prof.gemini_api_key = (form.gemini_api_key.data or "").strip()
                sess.add(prof)
            else:
                for k, v in data.items():
                    setattr(prof, k, v)
                if HAS_PROFILE_ABOUT:
                    prof.about = (form.about.data or "").strip()
                if HAS_PROFILE_GEMINI:
                    prof.gemini_api_key = (form.gemini_api_key.data or "").strip()
            sess.commit()
            flash("Saved")
            return redirect(url_for("tailor"))
        return render_template("profile.html", form=form)

    @app.route("/tailor", methods=["GET", "POST"])
    @login_required
    def tailor():
        sess = SessionLocal()
        prof = sess.query(Profile).filter(Profile.user_id == current_user.id).one_or_none()
        if not prof or not (prof.full_name or "").strip():
            flash("Please complete your profile (full name is required).")
            return redirect(url_for("profile"))

        form = TailorForm()
        if request.method == "POST" and form.validate_on_submit():
            url = (form.url.data or "").strip()
            if not url:
                flash("Paste a job posting URL.")
                return render_template("tailor.html", form=form)

            profile_dict = {
                "full_name": prof.full_name,
                "city": prof.city,
                "email": prof.email or current_user.email,
                "phone": prof.phone,
                "linkedin": prof.linkedin,
                "github": prof.github,
                "about": (getattr(prof, "about", None) if HAS_PROFILE_ABOUT else None),
                "gemini_api_key": (
                    getattr(prof, "gemini_api_key", None) if HAS_PROFILE_GEMINI else None
                ),
            }

            try:
                result = generate_both(profile_dict, url)
            except Exception as e:
                flash("Failed to generate resume. Check server dependencies (WeasyPrint).")
                return render_template("tailor.html", form=form)

            tmpdir = tempfile.mkdtemp(prefix="autotailor_")
            pdf_path = os.path.join(tmpdir, result["filenames"]["pdf"])  # absolute
            docx_path = os.path.join(tmpdir, result["filenames"]["docx"])  # absolute
            with open(pdf_path, "wb") as f:
                f.write(result["pdf"])
            with open(docx_path, "wb") as f:
                f.write(result["docx"])

            row = GeneratedResume(
                user_id=current_user.id,
                job_url=url,
                pdf_path=pdf_path,
                docx_path=docx_path,
                pdf_name=result["filenames"]["pdf"],
                docx_name=result["filenames"]["docx"],
                coverage_json=json.dumps(result.get("coverage", {})),
            )
            sess.add(row)
            sess.commit()
            return redirect(url_for("result", id=row.id))

        recent = (
            sess.query(GeneratedResume)
            .filter(GeneratedResume.user_id == current_user.id)
            .order_by(GeneratedResume.created_at.desc())
            .limit(5)
            .all()
        )
        del_form = DeleteResumeForm()
        return render_template("tailor.html", form=form, recent=recent, del_form=del_form)

    @app.route("/result/<int:id>")
    @login_required
    def result(id: int):
        sess = SessionLocal()
        row = sess.get(GeneratedResume, id)
        if not row or row.user_id != current_user.id:
            abort(404)
        coverage = json.loads(row.coverage_json or "{}")
        host = urlparse(row.job_url).hostname or "job"
        del_form = DeleteResumeForm()
        return render_template("result.html", row=row, coverage=coverage, host=host, del_form=del_form)

    @app.route("/delete/<int:id>", methods=["POST"])
    @login_required
    def delete_resume(id: int):
        form = DeleteResumeForm()
        if not form.validate_on_submit():
            abort(400)
        sess = SessionLocal()
        row = sess.get(GeneratedResume, id)
        if not row or row.user_id != current_user.id:
            abort(404)
        # Attempt to remove files
        for p in [row.pdf_path, row.docx_path]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        # Optionally try to remove the temp directory if empty
        try:
            d = os.path.dirname(row.pdf_path or "")
            if d and os.path.isdir(d) and len(os.listdir(d)) == 0:
                os.rmdir(d)
        except Exception:
            pass
        # Delete DB row
        sess.delete(row)
        sess.commit()
        flash("Deleted resume")
        return redirect(url_for("tailor"))

    @app.route("/download/<int:id>/<kind>")
    @login_required
    def download(id: int, kind: str):
        sess = SessionLocal()
        row = sess.get(GeneratedResume, id)
        if not row or row.user_id != current_user.id:
            abort(404)
        if kind == "pdf":
            return send_file(row.pdf_path, as_attachment=True, download_name=row.pdf_name)
        elif kind == "docx":
            return send_file(row.docx_path, as_attachment=True, download_name=row.docx_name)
        abort(404)

    @app.route("/logout")
    def logout():
        if current_user.is_authenticated:
            logout_user()
        return redirect(url_for("signin"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_ENV") == "development")
