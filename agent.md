# Agent Guide — AutoTailor

## Purpose
Generate tailored resumes from a job URL and a saved profile. MVP ships a stub that returns valid PDF & DOCX; real engine will replace stub later with the same function signature.

## Architecture
- Flask monolith + SQLite (dev).
 - Routes: /signin, /verify, /profile, /tailor, /result/<id>, /download/<id>/<kind>, /delete/<id>, /logout.
- Auth: email + 6-digit dev code (printed to console).
- Engine contract: adapters/resume_engine.py::generate_both(profile, jd_url) -> {pdf, docx, filenames, coverage}.
- Exports: WeasyPrint (PDF), python-docx (DOCX).
- CSS normalization: 16px inputs; consistent labels; no modals.

## Data Model
- User(email, verified, verify_code, verify_expiry[UTC, tz-aware])
- Profile(full_name, city, email, phone, linkedin, github, about, gemini_api_key)
- GeneratedResume(job_url, pdf_path, docx_path, pdf_name, docx_name, coverage_json, created_at[UTC])

## Boundaries & Contracts
- Web app treats engine as a black box returning bytes + filenames + coverage.
- Real engine must keep the same function signature.

## Security
- CSRF on POST forms; minimal logging; no PII in logs.
- Jinja autoescape enabled.

## Dev Run
- venv → pip install → python app.py → http://127.0.0.1:5000/signin

## Changelog
- 2025-08-29: Initial MVP with stub engine, downloads, and coverage badge.
- 2025-08-29: UI black buttons/links, larger brand title; combined website field; added About and Gemini API Key fields; stub now includes About in exports.
- 2025-08-29: Added deletion of generated resumes (files + DB row) via POST /delete/<id> with CSRF, UI buttons on Result and Tailor pages.

## TODO (post-MVP)
- Migrations (Alembic), Postgres prod, real engine, email provider.

Rule: every PR that changes routes, models, or engine contract must update agent.md (Architecture/Data Model/Boundaries/Changelog).
