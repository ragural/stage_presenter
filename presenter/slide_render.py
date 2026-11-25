"""Render slides to PNG images.

Strategy (first that works wins):
  1. Microsoft PowerPoint via COM (Windows, best fidelity)
  2. LibreOffice (soffice) -> PDF -> PNG via PyMuPDF if installed
  3. Give up: the UI falls back to a text-rendered slide.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)
_com_lock = threading.Lock()


def _render_powerpoint(pptx: Path, out_dir: Path, width: int) -> int:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    with _com_lock:
        pythoncom.CoInitialize()
        app = None
        pres = None
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            # ReadOnly, Untitled, WithWindow=False
            pres = app.Presentations.Open(str(pptx.resolve()), True, False, False)
            ratio = pres.PageSetup.SlideHeight / pres.PageSetup.SlideWidth
            height = int(width * ratio)
            count = pres.Slides.Count
            for i in range(1, count + 1):
                pres.Slides(i).Export(str((out_dir / f"{i}.png").resolve()), "PNG", width, height)
            return count
        finally:
            if pres is not None:
                pres.Close()
            if app is not None:
                try:
                    if app.Presentations.Count == 0:
                        app.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()


def _render_libreoffice(pptx: Path, out_dir: Path, width: int) -> int:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                     r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
            if os.path.exists(cand):
                soffice = cand
                break
    if not soffice:
        raise RuntimeError("LibreOffice not found")
    import fitz  # PyMuPDF  # type: ignore

    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)],
                   check=True, timeout=300, capture_output=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    doc = fitz.open(pdf)
    for i, page in enumerate(doc, start=1):
        zoom = width / page.rect.width
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(out_dir / f"{i}.png")
    n = len(doc)
    doc.close()
    pdf.unlink(missing_ok=True)
    return n


def render_slides(pptx: Path, out_dir: Path, width: int = 1600) -> bool:
    out_dir.mkdir(parents=True, exist_ok=True)
    renderers = []
    if sys.platform == "win32":
        renderers.append(("PowerPoint", _render_powerpoint))
    renderers.append(("LibreOffice", _render_libreoffice))
    for name, fn in renderers:
        try:
            n = fn(pptx, out_dir, width)
            log.info("Rendered %d slides with %s", n, name)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("%s rendering failed: %s", name, e)
    return False
