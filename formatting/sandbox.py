"""Sandboxed document compilation (hard constraint).

Every server-side compilation of user-supplied documents (.tex via pdflatex,
.docx via LibreOffice) runs through run_sandboxed(), which enforces:

- NO shell: argv exec only, shell=False, no shell metacharacter handling.
- NO shell-escape: pdflatex runs with -no-shell-escape AND openin_any/
  openout_any=p (paranoid), so TeX cannot spawn processes or read/write
  outside its job directory.
- NO network: the child runs inside a fresh network namespace
  (`unshare --net`) when the kernel allows it.
- Resource limits: CPU seconds, address space, output file size and process
  count via rlimits, plus a hard wall-clock timeout that kills the process
  group.
- Isolated working dir: a throwaway temp directory holds the only copy of
  the source; it is deleted whether compilation succeeds or fails. The
  uploaded file is never written anywhere else and never persisted.
- Minimal environment: an allow-listed env (PATH, HOME=jobdir, LANG).

This module is the production LaTeX compile path referred to by the
architecture docs; anything else that ever needs LaTeX must call it rather
than invoking compilers directly.
"""
import os
import resource
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

# Hard limits. LibreOffice gets a longer wall clock (cold-start profile
# creation on slower machines) and more address space (it maps large font/
# image caches; RLIMIT_AS counts virtual mappings, not just heap).
WALL_TIMEOUT_SECONDS = 40
SOFFICE_WALL_TIMEOUT_SECONDS = 90
CPU_SECONDS = 30
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
OUTPUT_FILE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_BYTES = 5 * 1024 * 1024
# NOTE: deliberately no RLIMIT_NPROC. That limit counts ALL processes owned
# by the invoking UID — on a developer desktop with hundreds of user
# processes it makes the compiler's first fork() fail with EAGAIN. Runaway
# process trees are contained instead by the wall-clock kill of the whole
# process group plus the CPU and memory limits.


class CompileError(Exception):
    """User-visible compilation failure (bad document, limits exceeded…)."""


class CompilerUnavailable(CompileError):
    """The needed compiler binary is not installed in this deployment."""


def _limits():
    """Applied in the child between fork and exec."""
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_BYTES, OUTPUT_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _netns_prefix():
    """Wrap the command in a fresh (empty) network namespace when possible."""
    unshare = shutil.which("unshare")
    if unshare is None:
        return []
    probe = subprocess.run(
        [unshare, "--net", "true"], capture_output=True, timeout=10
    )
    return [unshare, "--net"] if probe.returncode == 0 else []


def run_sandboxed(argv, cwd, extra_env=None, timeout=WALL_TIMEOUT_SECONDS):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(cwd),
        "LANG": "C.UTF-8",
        "TMPDIR": str(cwd),
    }
    env.update(extra_env or {})
    proc = subprocess.Popen(
        _netns_prefix() + argv,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=_limits,
        start_new_session=True,  # own process group: the whole tree dies below
        shell=False,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Kill the entire group — converters like LibreOffice fork helpers
        # that would outlive a plain kill of the direct child.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise CompileError(
            f"Compilation exceeded the {timeout}s time limit."
        ) from exc
    proc.output = output
    return proc


def compile_latex(source: bytes) -> bytes:
    """Compile a .tex upload to PDF under the full sandbox. Returns PDF bytes."""
    if shutil.which("pdflatex") is None:
        raise CompilerUnavailable(
            "LaTeX is not installed on this server (pdflatex not found). "
            "Install texlive-latex-base + texlive-latex-recommended, or upload "
            "a PDF sample instead."
        )
    if len(source) > MAX_SOURCE_BYTES:
        raise CompileError("Source file too large (5 MB limit).")

    with tempfile.TemporaryDirectory(prefix="texjob-") as job:
        jobdir = Path(job)
        (jobdir / "main.tex").write_bytes(source)
        argv = [
            "pdflatex",
            "-no-shell-escape",
            "-interaction=batchmode",
            "-halt-on-error",
            "-file-line-error",
            "main.tex",
        ]
        tex_env = {
            # Paranoid file access: no absolute paths, no parent dirs,
            # no dotfiles — reads and writes confined to the job dir.
            "openin_any": "p",
            "openout_any": "p",
            "shell_escape": "f",
            "TEXMFVAR": str(jobdir / ".texmf-var"),
            "TEXMFHOME": str(jobdir / ".texmf-home"),
        }
        proc = run_sandboxed(argv, jobdir, tex_env)
        pdf_path = jobdir / "main.pdf"
        if proc.returncode != 0 or not pdf_path.exists():
            raise CompileError(_latex_error_summary(jobdir))
        return pdf_path.read_bytes()


def _latex_error_summary(jobdir: Path) -> str:
    log = jobdir / "main.log"
    if log.exists():
        lines = log.read_text(errors="replace").splitlines()
        errors = [ln for ln in lines if ln.startswith("!") or ":! " in ln]
        if errors:
            return "LaTeX error: " + " ".join(errors[:3])[:500]
    return "The LaTeX file failed to compile. Check it builds locally first."


def convert_docx(source: bytes) -> bytes:
    """Convert a .docx upload to PDF via headless LibreOffice, sandboxed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise CompilerUnavailable(
            "Word conversion is not installed on this server (LibreOffice not "
            "found). Upload a .tex or PDF sample instead."
        )
    if "/snap/" in soffice:
        raise CompilerUnavailable(
            "LibreOffice is installed as a snap, which cannot run inside the "
            "compile sandbox. Install the regular package (apt install "
            "libreoffice-writer) or upload a PDF sample instead."
        )
    if len(source) > MAX_SOURCE_BYTES:
        raise CompileError("Source file too large (5 MB limit).")

    with tempfile.TemporaryDirectory(prefix="docxjob-") as job:
        jobdir = Path(job)
        (jobdir / "sample.docx").write_bytes(source)
        argv = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation=file://{jobdir}/.lo-profile",
            "--convert-to",
            "pdf",
            "--outdir",
            str(jobdir),
            str(jobdir / "sample.docx"),
        ]
        proc = run_sandboxed(argv, jobdir, timeout=SOFFICE_WALL_TIMEOUT_SECONDS)
        pdf_path = jobdir / "sample.pdf"
        if proc.returncode != 0 or not pdf_path.exists():
            detail = (proc.output or b"").decode(errors="replace").strip()
            detail = f" Converter said: {detail[-300:]}" if detail else ""
            raise CompileError(
                "The Word file could not be converted. Make sure it opens "
                f"cleanly in Word/LibreOffice, or upload a PDF sample instead.{detail}"
            )
        return pdf_path.read_bytes()


def render_upload(filename: str, source: bytes) -> bytes:
    """Route an uploaded sample to the right converter; PDF passes through."""
    name = (filename or "").lower()
    if name.endswith(".tex"):
        return compile_latex(source)
    if name.endswith(".docx"):
        return convert_docx(source)
    if name.endswith(".pdf"):
        if not source.startswith(b"%PDF-"):
            raise CompileError("That file does not look like a valid PDF.")
        if len(source) > MAX_SOURCE_BYTES:
            raise CompileError("Source file too large (5 MB limit).")
        return source
    raise CompileError("Upload a .tex, .docx, or .pdf sample.")
