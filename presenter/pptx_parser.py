"""Extract titles, visible text and speaker notes from a .pptx file."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

from pptx import Presentation
from pptx.util import Emu


@dataclass
class SlideInfo:
    index: int  # 1-based
    title: str
    bullets: list[str] = field(default_factory=list)
    notes: str = ""
    image: str | None = None  # relative URL of rendered slide image
    script: str = ""  # generated stage script

    def to_dict(self) -> dict:
        return asdict(self)


def _shape_texts(shape) -> list[str]:
    out: list[str] = []
    if shape.shape_type == 6 and hasattr(shape, "shapes"):  # group
        for s in shape.shapes:
            out.extend(_shape_texts(s))
        return out
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for p in shape.text_frame.paragraphs:
            t = "".join(r.text for r in p.runs).strip()
            if t:
                out.append(t)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                out.append(" | ".join(cells))
    return out


def parse_pptx(path: str) -> tuple[list[SlideInfo], tuple[int, int]]:
    prs = Presentation(path)
    slides: list[SlideInfo] = []
    for i, slide in enumerate(prs.slides, start=1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        bullets: list[str] = []
        for shape in slide.shapes:
            if slide.shapes.title is not None and shape.shape_id == slide.shapes.title.shape_id:
                continue
            bullets.extend(_shape_texts(shape))
        if not title and bullets:
            title = bullets.pop(0)
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        slides.append(SlideInfo(index=i, title=title or f"Slide {i}", bullets=bullets, notes=notes))
    size = (Emu(prs.slide_width).inches, Emu(prs.slide_height).inches)
    return slides, size
