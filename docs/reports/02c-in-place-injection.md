# Report — Module 2 revision 2: in-place template injection

Replaces extract-and-reconstruct with in-place content injection: the
institution's own (sanitized) Word/LaTeX file is stored as the template and
papers are generated inside it. Requested change; the binding security rule
is now "never execute arbitrary uploaded documents as code", enforced by
unconditional category-stripping (Word) and the compile sandbox (LaTeX —
no shell-escape, no network, resource/time limits, no exceptions).

## What changed

| Old (extract & reconstruct) | New (in-place injection) |
| --- | --- |
| Measure fonts/margins/spacing from rendered PDF, rebuild paper with generic renderer | Store the sanitized upload; replace only the dummy-question region with real content |
| Lossy (logos, banners, tables, colors lost) | Byte-preserving outside the question region |
| Side-by-side review + adjustment controls | Single preview of *your document* with sample questions; "Looks right" to save |
| Upload never stored | Sanitized upload stored as the live template (revised constraint, flagged) |
| .pdf samples accepted | .docx / .tex only (can't inject into a PDF) |

New components: `sanitize.py` (unconditional macro/DDE/OLE/external-rel
stripping by category), `inject_docx.py` and `inject_tex.py` (region
detection + prototype-cloning injection), `ingest.py` (orchestration),
region-pointing UI for ambiguous detections, Word download of generated
papers. Reused as-is: the compile sandbox, question/marks/option patterns,
the tenancy/draft/review plumbing, and the built-in ReportLab renderer
(still used for slot-based templates and all marking schemes).

## How you can verify it

1. **Paper formatting → Templates → + From sample file**, upload your exam
   .docx (dummy questions with A)/B)/C) options) — or
   `formatting/tests/fixtures/sample.tex`.
2. The preview that opens IS your document with sample questions injected.
   Click **Looks right — save template**.
3. Open a paper → **Edit details** → pick the new template → **Question
   paper PDF**. The PDF is your document with the paper's real bank
   questions; **Word (.docx)** downloads the editable file. Recognized
   cover fields (Class/Subject → paper title, Date, Time/Schedule →
   duration) are auto-filled; Name/ID are blanked for candidates.
4. Ambiguity path: upload a doc with only one recognizable question and
   you'll get the "Where are the dummy questions?" picker instead.

`python -m pytest -q` → **73 passing**, including: category-stripping
(macros/OLE/ActiveX gone, DDE/INCLUDE neutralized, external rels dropped,
hyperlinks kept); region detection (confident/ambiguous/manual); injection
(dummy content replaced, header/styles/other zip parts byte-identical,
option label style follows the dummy, Word auto-numbering preserved without
literal numbers, fields remapped, XML/LaTeX escaping); the tex
marks→vspace rule; sandboxed compile of injected LaTeX; and the full view
flow with tenant isolation.

## What did not carry over (flagged)

- **Geometric extraction is gone** (`extract.py`, `labeling.py`,
  pdfminer dependency, and the measurement round-trip tests): with the
  original file preserved, there is nothing to re-derive.
- **Plain-language adjustment controls** (margins/spacing/font) are gone
  for uploaded templates — the file is authoritative; to change formatting,
  edit the document and re-upload. Slot-based (no-upload) templates keep
  their editable named slots.
- **.pdf sample uploads** are gone (nothing to inject into).
- **Marks-proportional answer space in Word**: carried over for LaTeX (the
  dummy's `\vspace` values yield a per-mark rule) but in .docx the essay
  answer space clones the dummy question's own blank lines/answer box
  as-is, not scaled by marks. Scaling by cloning N blank paragraphs is a
  possible later refinement.
- **Marks display follows the dummy**: if your dummy questions don't show
  "[n marks]", generated questions won't either (that matches your
  template's convention; add marks to one dummy question to opt in).
- **Paper-level instruction text is not injected** into source templates —
  the document's own authored instructions stay. (The built-in renderer
  still injects per-paper instructions.)

## Notes on the constraint changes (explicit)

- The uploaded file is now stored and reused — after unconditional
  sanitization; the pre-sanitization original is never written anywhere.
- Institutional LaTeX is compiled on every generation — always inside the
  sandbox. TeX is code; the sandbox exists precisely to contain it.
- Generation cost: each question-paper download compiles/converts
  (~1–8s). Acceptable now; a per-(paper, template, content-hash) cache is
  the obvious later optimization.
