# AutoTailor

A tiny web app where users sign up with a 6-digit email code, create a profile, paste a job posting URL, click Go, and download a PDF or DOCX resume. For MVP, resume content is stubbed (but valid files); the real AI “resume engine” will replace the stub later without changing the web app.

## Dev setup

Requirements: Python 3.11+

```
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
export FLASK_ENV=development SECRET_KEY=dev_key
python app.py
# open http://127.0.0.1:5000/signin
```

Notes:
- WeasyPrint requires Cairo/Pango system libraries. On macOS, use Homebrew to install `cairo`, `pango`, and `gdk-pixbuf`.
- In development, the 6-digit code is printed to the console after submitting your email on the sign-in page.


```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

`DATABASE_URL` is supported; falls back to `sqlite:///resume.db`.

