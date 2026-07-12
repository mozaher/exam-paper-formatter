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

**How the "sandboxed LaTeX" constraint is met here: there is no LaTeX.** The
PDF builder (`formatting/pdf.py`) is pure-Python ReportLab — no subprocess, no
shell, no external compiler, no network, fully deterministic. The constraint
exists to contain the risk of compiling user-influenced markup server-side;
this module eliminates that risk class instead of containing it. LaTeX first
genuinely enters the system with the Paper MCQ module (AMC requires it), where
it will run inside AMC's own sandboxed, separate-process container.

**Templates are named slots, per the hard constraint.** A `PaperTemplate` is a
row of data — institution name, subtitle, footer text, default instructions,
font choice, paper size. No file uploads, nothing executable, and every slot
value plus all question text is XML-escaped before it reaches the layout
engine (covered by a test that feeds hostile markup).

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
- **Sandboxed LaTeX.** The formatting (and later paper-MCQ typesetting) modules
  will compile LaTeX in a sandbox: no shell-escape, containerized, no network,
  with CPU/memory/time limits. Users pick from a **fixed set of named template
  slots**, not arbitrary uploaded LaTeX/Word — templates are data filling known
  slots, never executable input.
- **AI proposes, a deterministic step executes.** The blueprint generator (and
  any AI-assisted selection/formatting) will emit a **reviewable spec** (e.g. a
  table of specification as data) that a human can approve and a deterministic
  builder then executes — the model never directly emits the graded/printed
  artifact. Consistency and auditability over saving a step.

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
5. **PDF engine: ReportLab (pure Python) over server-side LaTeX or
   HTML-to-PDF.** LaTeX would demand the full sandbox apparatus (container, no
   shell-escape, resource limits) plus a ~1 GB TeX distribution in every
   deployment; WeasyPrint needs system C libraries (Pango/Cairo) that
   complicate `pip install` for non-developers. ReportLab installs everywhere
   as a wheel, runs in-process with zero attack surface from external
   compilers, and its output is deterministic. *Affects: security (eliminates
   the LaTeX risk class in this module), cost (no heavyweight runtime), and
   timeline.* Trade-off: no LaTeX-grade math typesetting in formatted papers
   yet; if that becomes a requirement it will be added via the same sandboxed
   LaTeX service that Paper MCQ will already need — not by weakening this
   module.
6. **Marking scheme as a first-class second output.** The same paper renders a
   candidate PDF and a staff PDF, and tests assert answers never reach the
   candidate version. *Affects: security/correctness of the product's core
   promise.*

## Testing

`python -m pytest -q` runs the suite (35 tests). It's deliberately weighted
toward the guarantees that are expensive to get wrong: cross-tenant isolation,
plan gating, QTI round-trip + import safety, and candidate/marking-scheme
separation in generated PDFs.
