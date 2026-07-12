# Architecture & assumptions

This document explains how the platform is put together, records the decisions
made so far, and shows where each of your hard constraints lives in the design
(including the ones for modules not yet built, so the shape is committed to now
rather than retrofitted).

## Stack

- **Django 5.2** (Python 3.11). Chosen for batteries-included auth, an admin,
  the ORM, migrations, and a mature security posture — the fastest way to a
  *safe* multi-tenant app, and it keeps every module in one deployable unit
  while staying modular in code.
- **SQLite** in dev, **Postgres** in production (via `DATABASE_URL`). No code
  differs between them.
- **WhiteNoise** serves static files so the app runs as a single process with no
  separate web server needed.
- **gunicorn** as the production WSGI server.

Server-rendered HTML (Django templates) with a single stylesheet — no SPA. This
keeps the surface small and auditable. Modules that genuinely need rich
client-side behaviour (the timed online exam) can add scoped JavaScript without
changing this baseline.

## Core vs. modules

```
core (shared spine)
 ├─ tenancy:   Organization, Membership, TenantOwnedModel + org-scoped manager
 ├─ accounts:  custom email User, Invitation, signup/invite flows
 ├─ billing:   Plan, Subscription, provider interface, feature gating
 └─ registry:  core/modules.py — modules self-register; nav/dashboard read it

modules (each a Django app; may depend on core and read the item bank — the
content spine — but never import a sibling feature module)
 ├─ itembank    ← built (module 1)
 ├─ formatting  ← built (module 2)
 └─ paper_mcq, online_exam, blueprint  ← planned (shown as "coming soon")
```

**Module contract.** A module:
1. is its own Django app under `INSTALLED_APPS`;
2. registers a `Module(...)` from its `AppConfig.ready()` (code, name, landing
   URL, optional dashboard stat);
3. stores tenant data on models extending `core.models.TenantOwnedModel`
   (an org FK + a `.for_org(org)` manager);
4. gates its views with `core.access.ModuleRequiredMixin` (tenancy + plan
   check).

Because modules never import each other, adding one is additive. Cross-module
needs (e.g. the formatting and paper-MCQ modules both reading questions) go
**through the item bank's models/queries**, which is exactly why the bank is the
first module and the "spine everything reads from."

## Multi-tenancy (data isolation)

- Every content row has an `org` foreign key (`TenantOwnedModel`).
- Queries go through `Model.objects.for_org(org)`. Views resolve the current org
  from `request.org` (set by `ActiveOrganizationMiddleware` from the signed-in
  user's membership) and never trust an org id from the request body/URL.
- Object lookups are scoped, so requesting another tenant's row returns **404**,
  not a forbidden-with-existence-leak.
- This is covered by tests in `core/tests/test_tenancy.py` (read, edit, and
  cascade-delete isolation).

v1 assumes **one organization per user** (the middleware picks the first
membership). The membership model is many-to-many already, so a session-based
org switcher is a later, non-breaking addition — downstream code only reads
`request.org`.

## Accounts & auth

- Custom `User` keyed by email (no username). Django's own password hashing,
  validators, session and CSRF protection.
- **Invitations** are tokenized links. No SMTP is assumed in this phase: the
  accept link is shown to the admin to share out-of-band, and password-reset
  emails print to the server console. Swapping in a real `EMAIL_BACKEND` is a
  settings change, no code change.
- Roles: **owner / admin / teacher**. Owner/admin manage the org and billing.

## Billing & plan gating

- `Plan.features` is JSON: `{"modules": [...], "limits": {"max_items": N}}`.
- An org's effective plan = its active `Subscription`'s plan, else the default
  (Free) plan. All access is resolved through `core/billing.py`
  (`has_module`, `get_limit`), so gating is in one place.
- **Provider interface.** `BillingProvider.change_plan()` is implemented by
  `ManualBillingProvider` (used now: instant plan changes, no payment) and
  stubbed by `StripeBillingProvider`. Moving to real payments means implementing
  the Stripe checkout + webhook flow behind that interface; **callers don't
  change.** This was a deliberate fork (below).

## Item bank & QTI 3.0 portability

- Question types live in a **registry** (`itembank/itemtypes.py`): each type
  declares whether it has choices, a model answer, and how to validate. Adding
  true/false, matching, numeric, etc. later is a registration plus QTI
  (de)serialization — no schema or template rewrites. MCQ and essay ship now.
- **Portability is a hard constraint, and it's met with the QTI 3.0 standard,
  not a custom format.** Export produces an IMS content package (zip +
  `imsmanifest.xml`) of QTI 3.0 assessment items. Import accepts that package or
  a single item XML.
  - Interoperable parts — stem, choices, correct response, max score, marking
    rubric — use standard QTI 3.0 markup, so other QTI-3 tools can read them.
  - Bank-specific tags QTI has no standard slot for (topic, difficulty,
    cognitive level) ride in the manifest's per-resource metadata under our own
    XML namespace. Other QTI tools ignore them; our importer round-trips them.
  - Import is **namespace-tolerant** (matches by local element name) so files
    from other producers still import.

### Security of import (hard constraint groundwork)

Uploaded XML is parsed with **defusedxml** — no entity expansion, no external
DTD/entity fetches — which blocks billion-laughs and XXE/SSRF. There's a test
(`test_import_rejects_xxe_entities`) that feeds a `file:///etc/passwd` entity and
asserts it's rejected. Upload size is capped. This matters now (untrusted
uploads) and sets the pattern for later modules that ingest files.

## Paper formatting module (module 2)

Assembles an exam paper from bank questions (Paper → Sections → placed
questions, with optional per-paper marks overrides) and renders two PDFs: the
candidate question paper and a staff marking scheme.

**Production rendering has no LaTeX.** The PDF builder (`formatting/pdf.py`)
is pure-Python ReportLab — no subprocess, no shell, no external compiler, no
network, fully deterministic. Every exam paper ever generated comes from this
renderer consuming a bounded formatting spec plus named content slots.

**Templates are named slots, per the hard constraint.** A `PaperTemplate` is a
row of data — text slots (institution name, subtitle, footer, instructions)
plus a bounded numeric layout spec (font, sizes, margins, spacing, the
marks-to-answer-space rule). Nothing executable is ever stored, and every slot
value plus all question text is XML-escaped before it reaches the layout
engine (covered by a test that feeds hostile markup).

### Template-by-example: in-place content injection

Staff create a template by uploading the Word (.docx) or LaTeX (.tex) file
their institution already uses, containing dummy questions where real ones
should go. **The (sanitized) uploaded file itself is stored as the template**,
and papers are generated by injecting content into it — everything outside
the question region (fonts, margins, styles, headers, logos, packages) is
preserved byte-for-byte from the original. This deliberately revises the
original "never store the upload" rule: the binding constraint is *don't
execute arbitrary uploaded documents as code*, which is enforced as follows.

1. **Unconditional sanitization** (`formatting/sanitize.py`, .docx only):
   macros (`vbaProject.bin`), OLE embeddings, ActiveX, DDE/INCLUDE field
   codes, attached-template references and non-hyperlink external
   relationships are stripped **by category, regardless of content** at
   ingest. Only sanitized bytes are stored, converted, or downloadable. Word
   files never execute server-side — LibreOffice only converts them, inside
   the sandbox.
2. **Region detection — the AI-assisted step** (`inject_docx.detect` /
   `inject_tex.detect`): locates the dummy-question region (and recognizable
   fill-in fields like Subject/Date/Time) by structural patterns — numbered
   paragraphs, A)/B)/C) options, marks indicators, Word auto-numbering. It
   identifies *where to inject*, not a spec to re-render from. Confident
   detections go straight to preview; ambiguous ones ask staff to point out
   the start/end of the questions area in a simple picker (never editing
   data). LaTeX authors can guarantee detection with
   `%%QUESTIONS_START`/`%%QUESTIONS_END` comments. An LLM detector can
   replace the pattern classifier behind the same contract; it would only
   ever propose region boundaries for staff to confirm visually.
3. **Injection** (`formatting/inject_docx.py`, `formatting/inject_tex.py`):
   the region's dummy paragraphs/lines act as **style prototypes** — cloned
   with new text so question/option/heading formatting (including Word
   auto-numbering and answer-box tables, or the dummy's own
   `\hfill [n marks]` + `\vspace` idiom with a marks-proportional rule) is
   exactly the institution's. All injected content is XML/LaTeX-escaped.
   Recognized `Label: value` fields outside the region (Subject, Date,
   Time…) are remapped to the paper's values; unrecognized ones are left
   untouched.
4. **Sandboxed rendering** (`formatting/sandbox.py`) — unchanged and applied
   with no exceptions: injected .tex compiles under pdflatex with
   `-no-shell-escape` + paranoid file access; injected .docx converts under
   headless LibreOffice; both with no shell, allow-listed env, CPU/memory/
   file-size limits, group-wide wall-clock kill, and a fresh network
   namespace. Tests prove `\write18` has no side effects, path escapes fail,
   and runaway documents die.
5. **Review stays visual**: staff see *their own document* with sample
   questions injected — one click ("Looks right") saves it. No side-by-side
   is needed since the output IS the uploaded document; no raw data is ever
   shown. Word-source papers can also be downloaded as .docx for final
   touch-ups.

Templates created without an upload (and every **marking scheme**) use the
built-in deterministic ReportLab renderer with the bounded spec in
`formatting/spec.py` — no compilers in that path at all.

**Marking-scheme separation.** The candidate PDF provably never contains model
answers or the answer key — asserted by tests on the rendered bytes, since
leaking a marking guide into a printed exam is the worst failure mode this
module has.

## Where the not-yet-built hard constraints live

These modules aren't built yet, but the design already reserves the right shape
so they can't be "bolted on wrong" later:

- **AMC as a separate process (GPL boundary).** The Paper MCQ module will shell
  out to Auto Multiple Choice as a genuinely separate OS process (CLI + files),
  never linked in-process or via FFI. The `Dockerfile` header already flags that
  AMC does not belong in this Python image; it will be its own
  service/container. This is a licensing boundary as much as an engineering one.
- **Sandboxed LaTeX — now implemented** (`formatting/sandbox.py`, see above):
  no shell-escape, no network, resource/time limits, isolated temp dirs. Any
  future LaTeX need (e.g. AMC typesetting in Paper MCQ) must go through this
  runner or AMC's own container — never a bare compiler invocation.
- **AI proposes, a deterministic step executes — pattern now established** by
  the template-by-example flow (labels → spec → human confirmation →
  deterministic render). The blueprint generator will follow the same shape:
  the model emits a reviewable table-of-specification, a deterministic step
  fills it from the bank, flagging gaps rather than guessing.

## Forks in the road resolved so far

1. **Framework: Django (monolith-of-modules) vs. microservices.** Chose a single
   Django project with pluggable apps. Rationale: fastest path to *safe*
   multi-tenancy and auth, one thing to deploy, and modularity enforced in code
   (the module contract) rather than by network boundaries you don't need yet.
   The one place a separate process is mandatory — AMC — is kept separate for
   licensing/isolation reasons regardless. *Affects: timeline (faster), security
   (mature defaults).*
2. **Billing now vs. later.** Built the plan/subscription model and a provider
   interface now, but shipped only a **manual** provider (no payment
   integration, no API keys — none are available in this environment, and
   charging money shouldn't be stubbed carelessly). Real Stripe billing is a
   contained future change behind the interface. *Affects: cost/timeline — no
   payment-integration effort spent before there's a product to charge for;
   security — no secret handling added prematurely.*
3. **QTI metadata that the standard doesn't define (topic/difficulty/cognitive
   level).** Rather than bend standard fields or invent a wholly custom format,
   these travel in manifest metadata under our namespace. *Affects: portability
   — files stay valid QTI 3.0 and interoperable, while nothing is lost on
   round-trip within our system.*
4. **Custom email `User` from day one.** Adding it later forces a painful
   migration, so it's in `0001`. *Affects: timeline — a minute now vs. a
   migration headache later.*
5. **PDF engine: ReportLab (pure Python) for all production rendering.**
   WeasyPrint needs system C libraries (Pango/Cairo) that complicate
   `pip install` for non-developers; LaTeX-as-renderer would put a compiler in
   the hot path of every exam. ReportLab installs everywhere as a wheel, runs
   in-process, and its output is deterministic. LaTeX/LibreOffice exist in the
   stack only to render *uploaded samples once* during template extraction —
   inside the sandbox runner, never for production paper generation. Compilers
   are optional at deploy time: without them, staff can still upload .pdf
   samples (extraction is identical). Trade-off: no LaTeX-grade math
   typesetting in formatted papers yet.
6. **Marking scheme as a first-class second output.** The same paper renders a
   candidate PDF and a staff PDF, and tests assert answers never reach the
   candidate version. *Affects: security/correctness of the product's core
   promise.*

## Testing

`python -m pytest -q` runs the suite (35 tests). It's deliberately weighted
toward the guarantees that are expensive to get wrong: cross-tenant isolation,
plan gating, QTI round-trip + import safety, and candidate/marking-scheme
separation in generated PDFs.
