"""Compile the corrected paper and verify that the committed PDF is current.

Run this script explicitly after rebuilding publication data. Imports perform
no compilation or file writes. --check validates saved hashes without TeX.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
PUBLICATION_DATE = "2026-11-10"
SOURCE_DATE_EPOCH = int(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp())
MANIFEST_PATH = ROOT / "paper" / "publication_manifest.json"
PDF_PATH = ROOT / "paper" / "main.pdf"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    """Include the exact authored source, generated evidence and build recipe."""
    paths = [ROOT / "paper" / "main.tex", ROOT / "paper" / "generated_results.tex",
             ROOT / "paper" / "prototype_results.tex",
             ROOT / "CITATION.cff", ROOT / "artifacts" / "publication_manifest.json",
             ROOT / "artifacts" / "prototype_manifest.json",
             Path(__file__).resolve()]
    paths.extend(sorted((ROOT / "figures").glob("*.pdf")))
    return {path.relative_to(ROOT).as_posix(): _sha256(path) for path in paths}


def check_paper():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("publication_version") != VERSION:
        raise ValueError("unsupported paper manifest version")
    if manifest.get("source_sha256") != source_hashes():
        raise ValueError("paper inputs changed; run python scripts/build_paper.py")
    if manifest.get("output_sha256") != {"paper/main.pdf": _sha256(PDF_PATH)}:
        raise ValueError("paper PDF changed or is stale; rebuild the paper")
    return manifest


def build_paper():
    engine = shutil.which("pdflatex")
    if engine is None:
        raise ValueError("pdflatex is required to render the paper; --check needs no TeX")
    # Fail before compilation when generated tables or charts do not match code.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "rebuild_publication.py"),
                    "--check"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "run_prototype_validation.py"),
                    "--check"], cwd=ROOT, check=True)
    sources_before = source_hashes()
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(SOURCE_DATE_EPOCH)
    environment["FORCE_SOURCE_DATE"] = "1"
    engine_version = subprocess.run([engine, "--version"], check=True,
                                    capture_output=True, text=True).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix="sphere-paper-") as temporary:
        directory = Path(temporary)
        # pdfTeX's default trailer ID incorporates its temporary output path.
        # Omitting that optional identifier prevents otherwise identical
        # builds in separate staging directories from changing PDF bytes.
        command = [engine, "-interaction=nonstopmode", "-halt-on-error", "-jobname=main",
                   "-file-line-error", "-output-directory", str(directory),
                   r"\pdftrailerid{}\input{main.tex}"]
        for attempt in range(3):
            result = subprocess.run(command, cwd=ROOT / "paper", env=environment,
                                    capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError("LaTeX compilation failed:\n" + result.stdout[-12000:])
            log = (directory / "main.log").read_text(encoding="utf-8", errors="replace")
            rerun = "Rerun to get cross-references right" in log or "Label(s) may have changed" in log
            if attempt >= 1 and not rerun:
                break
        if re.search(r"(?:Reference|Citation) .+ undefined|There were undefined (?:references|citations)", log):
            raise ValueError("paper has unresolved references or citations")
        if rerun:
            raise ValueError("paper cross-references did not converge in three passes")
        if sources_before != source_hashes():
            raise ValueError("paper inputs changed during rendering; retry after edits finish")
        staged_pdf = directory / "main.pdf"
        if not staged_pdf.read_bytes().startswith(b"%PDF-"):
            raise ValueError("LaTeX did not produce a valid PDF header")
        manifest = {
            "schema_version": 1,
            "publication_version": VERSION,
            "publication_date": PUBLICATION_DATE,
            "source_sha256": sources_before,
            "output_sha256": {"paper/main.pdf": _sha256(staged_pdf)},
            "environment": {"latex_engine": engine_version,
                            "python_version": platform.python_version(),
                            "source_date_epoch": SOURCE_DATE_EPOCH,
                            "force_source_date": True,
                            "pdf_trailer_identifier": "omitted for deterministic staging-directory builds"},
            "build_command": "python scripts/build_paper.py",
            "scope": "Exact authored paper, citation metadata, generated tables, figure PDFs and model manifest used to render this PDF. Visual review and scientific validity remain separate checks.",
        }
        # Source hashes are final before publishing either result.
        for destination, contents in (
                (PDF_PATH, staged_pdf.read_bytes()),
                (MANIFEST_PATH, (json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode("utf-8"))):
            descriptor, name = tempfile.mkstemp(prefix=".publication-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(contents)
                os.replace(name, destination)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        for warning in re.findall(r"(?:Overfull|Underfull) \\[hv]box[^\n]*", log):
            print(warning)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check saved PDF/input hashes without TeX")
    args = parser.parse_args(argv)
    try:
        manifest = check_paper() if args.check else build_paper()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    action = "Verified" if args.check else "Rendered"
    print(f"{action} publication v{manifest['publication_version']} paper and source manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
