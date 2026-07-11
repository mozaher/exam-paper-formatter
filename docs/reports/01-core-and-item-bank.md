# Report — Module 1: Platform core + Item bank

## What was built

**Platform core (the shared spine)**
- Multi-tenancy: `Organization` (tenant), `Membership` (user↔org with
  owner/admin/teacher roles), and a `TenantOwnedModel` base + org-scoped
  querysets that every content model uses. Tenants are isolated at the query
  layer.
- Accounts: custom email-based `User`, self-serve signup that provisions an org
  and an owner, and tokenized invite links (no email server required).
- Billing: `Plan` / `Subscription` with feature+limit gating in one place; a
  pluggable billing-provider interface with a working "manual" provider and a
  Stripe stub.
- Module registry: modules self-register; the dashboard and navigation build
  from it. Planned modules show as "coming soon."

**Item bank module**
- Author **MCQ** and **essay** questions; tag by topic, difficulty, cognitive
  level (Bloom) and marks; filter/search; topics with one subtopic level.
- A question-type registry so new types are additive.
- **QTI 3.0** import/export (IMS content package) from both the web UI and the
  CLI — the portability guarantee.
- Safe XML import (defusedxml: no XXE/entity expansion).

## How you can verify it

Full steps are in `README.md`. The short version:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
# open http://127.0.0.1:8000/ , sign in as teacher@demo.test / demopass123
```

Then, to see the guarantees as green tests:

```bash
python -m pytest -q      # 20 passing
```

And the same QTI round-trip from the CLI:

```bash
python manage.py export_qti --org demo-high-school --out /tmp/bank.zip
python manage.py import_qti --org demo-high-school --file /tmp/bank.zip
```

## Forks in the road I resolved

Full reasoning in `docs/architecture.md`. The ones that affected cost, security,
or timeline:

1. **Django monolith-of-modules over microservices** — fastest path to safe
   multi-tenancy/auth, one deployable, modularity enforced in code. AMC stays a
   separate process regardless (licensing).
2. **Billing modelled now, payments later** — shipped a manual provider behind a
   provider interface; no Stripe keys/secret-handling added before there's
   something to charge for. Real payments slot in without touching callers.
3. **Non-standard item metadata (topic/difficulty/cognitive level) via QTI
   manifest metadata** — keeps exports valid, interoperable QTI 3.0 while
   round-tripping losslessly in our system.
4. **Custom email `User` in the first migration** — avoids a painful later
   migration.

One bug found and fixed during end-to-end verification: deleting an organization
raised a `ProtectedError` when a topic had subtopics (self-referential FK was
`PROTECT`). Changed to `CASCADE` (user-facing deletes are still guarded at the
view), added a regression test, and confirmed tenant deletion and
`seed_demo --reset` are now clean/idempotent.
