# Exam Platform

A multi-tenant web app that helps teachers build and run exams. It is a shared
**core** (accounts, organizations/tenancy, billing, a module registry) plus
feature **modules** that plug into it.

**Build status — Modules 1–2 of 5 complete:** platform core + the **Item bank**
module, plus the **Paper formatting** module (exam-paper PDFs). The remaining
modules (Paper MCQ via AMC, Online exam, AI blueprint generator) are scaffolded
as "coming soon" and will be built in order, each with its own report.

---

## What's in this phase

| Area | What you get |
| --- | --- |
| **Tenancy** | Every account belongs to an **Organization** (a tenant). All content carries an org and is queried through an org-scoped manager, so tenants never see each other's data. |
| **Accounts** | Email-based sign-in. Sign up creates your org and makes you the owner. Owners/admins invite teachers via shareable links (no email server needed). |
| **Billing** | Plans (Free / Pro / Institution) gate which modules and limits an org gets. A "manual" billing provider lets admins switch plans instantly; a real payment provider (Stripe) plugs in behind the same interface. |
| **Item bank** | Author **MCQ** and **essay** questions, tag them with topic, difficulty, cognitive level (Bloom) and marks. Filter/search. Organize topics with one level of subtopics. |
| **Portability** | Import and export the whole bank as **QTI 3.0** (an IMS content package). Your content is never locked in. |
| **Paper formatting** | Assemble exam papers from bank questions into sections, set per-paper marks, and download two print-ready PDFs: the **question paper** (with ruled answer space for essays) and a staff-only **marking scheme** (correct answers + marking guides). Institutional branding comes from templates that are a fixed set of named slots — never uploaded LaTeX/Word files. |

---

## Run it yourself (about 2 minutes)

You need Python 3.11+. From the project directory:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up the database (SQLite, created automatically) and load demo data
python manage.py migrate
python manage.py seed_demo

# 3. Start the app
python manage.py runserver
```

Then open **http://127.0.0.1:8000/** and sign in with the demo account:

- **Email:** `teacher@demo.test`
- **Password:** `demopass123`

You'll land on the dashboard. Click **Paper formatting → Sample Midterm
Examination** and hit **Question paper PDF** / **Marking scheme PDF** to see the
two print-ready PDFs built from the seeded questions. Click **Item bank** to see
the three seeded questions (two MCQ, one essay). Try:

- **+ MCQ / + Essay** — author a new question.
- **Export** — download the bank as a QTI 3.0 `.zip`.
- **Import** — upload that same `.zip` into a bank (into another org it recreates
  everything; into the same org it detects duplicates and skips them).
- **Billing** — switch the demo org between plans and watch modules lock/unlock.
- **Organization** — invite a teacher; you'll get a shareable link to hand them.

To create your **own** organization instead of using the demo, go to
http://127.0.0.1:8000/signup/.

### See the isolation and portability guarantees pass as tests

```bash
python -m pytest -q
```

You should see **35 passing tests**. The ones that back up the headline claims:

- `core/tests/test_tenancy.py` — one org **cannot** read or edit another org's
  questions (returns 404, no data leak); plan gating and item limits are
  enforced; deleting a tenant removes all its content.
- `itembank/tests/test_qti_roundtrip.py` — export → import **round-trips**
  content *and* metadata; re-import skips duplicates; a malicious XML file with
  an external entity (XXE/SSRF attempt) is **rejected**, not processed.
- `core/tests/test_accounts.py` — signup provisions a tenant; invite links work
  and can't be reused.
- `formatting/tests/test_pdf.py` — the question paper **never** contains model
  answers; the marking scheme does; hostile markup in question text can't break
  (or script) the PDF builder.
- `formatting/tests/test_papers.py` — papers, sections, marks overrides, and
  cross-tenant isolation for papers and question-picking.

### Do the same round-trip from the command line

```bash
# Export one org's bank to a QTI 3.0 package
python manage.py export_qti --org demo-high-school --out /tmp/bank.zip

# Import a package (or a single item .xml) into an org
python manage.py import_qti --org demo-high-school --file /tmp/bank.zip
```

---

## Deploying

The app is 12-factor and reads its config from the environment (see
`.env.example`). It runs on SQLite locally and Postgres in production with no
code change.

- **Any Docker host:** `docker build -t exam-platform . && docker run -p 8000:8000 -e DJANGO_SECRET_KEY=... exam-platform`
- **Heroku/Railway/Render-style platforms:** a `Procfile` is included (runs
  migrations on release, serves with gunicorn). Set `DJANGO_DEBUG=0`,
  `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `DATABASE_URL`.

With `DJANGO_DEBUG=0`, HTTPS hardening (HSTS, secure cookies, SSL redirect) turns
on automatically. `python manage.py check --deploy` reports no issues.

---

## How it's structured (and why that matters for later modules)

```
config/        Django project (settings, urls, wsgi)
core/          Tenancy, accounts, billing, the module registry — the shared spine
itembank/      Module 1: the question bank + QTI 3.0 import/export
formatting/    Module 2: exam-paper assembly + deterministic PDF builder
templates/     Server-rendered pages
static/        One stylesheet
docs/          Architecture & assumptions, and the per-module reports
```

Each module is a self-contained Django app that depends on `core` but **never on
another module**. A module registers itself with the core's registry
(`core/modules.py`) and the navigation/dashboard build themselves from that.
Adding a new module later is: create an app, register it, add its URLs — with no
edits to existing modules. See `docs/architecture.md` for the full picture,
including the hard constraints (AMC as a separate process, sandboxed LaTeX,
AI-proposes/deterministic-executes, QTI portability) and how the design already
makes room for them.
```
