"""Turn slide content + speaker notes into a stage-ready spoken script.

Uses the local OpenVINO LLM (NPU/GPU/CPU) or any OpenAI-compatible endpoint
(see presenter/llm.py).
If no LLM is configured, a light-touch local polisher is used instead.
"""
from __future__ import annotations

import os
import re

from . import llm

TONES = {
    "inspirational": "uplifting, visionary and motivating; builds a sense of possibility",
    "authoritative": "confident, assured and decisive; speaks with earned expertise",
    "conversational": "warm, natural and approachable, as if speaking to each listener personally",
    "empathetic": "compassionate, human and sincere; acknowledges the audience's challenges",
    "persuasive": "compelling and reasoned; builds a clear case toward a call to action",
    "academic": "precise, measured and evidence-led; intellectually rigorous",
    "storytelling": "narrative and vivid; uses anecdote, imagery and suspense",
    "executive": "crisp, strategic and outcome-focused; respects senior leaders' time",
}

STYLES = {
    "keynote": "a polished conference keynote on a large stage",
    "ted": "a TED-style talk: one big idea, personal, memorable, with rhetorical questions",
    "boardroom": "a boardroom briefing to senior executives: headline first, then evidence",
    "lecture": "a university lecture: structured, explanatory, with signposting",
    "sales": "a client pitch: problem, value, proof, and a clear next step",
    "townhall": "an all-hands town hall: inclusive, transparent and energising",
    "technical": "a technical deep-dive to engineers: accurate, concrete, no fluff",
}

LENGTHS = {
    "concise": "about 60-90 words",
    "standard": "about 110-160 words",
    "elaborate": "about 180-250 words",
}

SYSTEM_PROMPT = """You are a world-class speechwriter and presentation coach.
You write words that a seasoned, articulate speaker delivers live on stage.
Your language is profound, mature and eloquent - yet always natural to say aloud.

Rules:
- Write ONLY the words to be spoken. No headings, stage directions, markdown, bullet points, emojis or quotation marks around the whole text.
- Faithfully convey every substantive point in the speaker notes and slide; never invent facts, numbers or names.
- Use rhetorical craft sparingly and tastefully: tricolons, contrast, a well-placed rhetorical question, a memorable closing line.
- Prefer short-to-medium sentences with natural rhythm and breathing points. Use commas and full stops to pace delivery.
- Do not say "this slide shows" or "on this slide". Speak to the audience, not about the slide.
- Spell out symbols naturally for speech (e.g. "percent", "versus"), and expand obscure abbreviations once.
- Provide a smooth, natural transition from the previous point when context is given. Do not repeat the previous slide's content.
- Register: mature gravitas and quiet conviction, like a respected statesman or seasoned CEO - never hype or salesmanship.
- Avoid cliches, buzzwords and folksy idioms (e.g. "game-changer", "proof is in the pudding", "move the needle", "battleground", "let's dive in", "ladies and gentlemen"). Find fresher, more precise language.
- Never write stage directions or cues such as "Pause", "(beat)", "[smile]" - pacing comes from punctuation alone.
"""


def llm_enabled() -> bool:
    return llm.enabled()


def _build_user_prompt(slide: dict, total: int, prefs: dict, prev_title: str | None,
                       prev_script: str | None) -> str:
    tone = prefs.get("tone", "authoritative")
    style = prefs.get("style", "keynote")
    length = prefs.get("length", "standard")
    parts = [
        f"Tone: {tone} - {TONES.get(tone, tone)}",
        f"Format: {STYLES.get(style, style)}",
        f"Target length: {LENGTHS.get(length, length)}",
        f"English variant: {prefs.get('locale', 'en-US')} (use this region's spelling and idiom, keep it globally understandable)",
    ]
    if prefs.get("audience"):
        parts.append(f"Audience: {prefs['audience']}")
    if prefs.get("instructions"):
        parts.append(f"Additional speaker instructions: {prefs['instructions']}")
    position = "opening" if slide["index"] == 1 else ("closing" if slide["index"] == total else "middle")
    parts.append(f"\nThis is slide {slide['index']} of {total} ({position} of the talk).")
    if position == "opening":
        parts.append("Open with a strong hook - a striking statement, a vivid image or a provocative question - "
                     "then set up the talk. Do NOT begin with 'Ladies and gentlemen', 'Good evening', "
                     "'Hello everyone' or any other stock greeting.")
    if position == "closing":
        parts.append("Bring the talk to a resonant close with a memorable final line and, if appropriate, a call to action.")
    if prev_title:
        parts.append(f"Previous slide title: {prev_title}")
    if prev_script:
        parts.append(f"Last sentences spoken: ...{prev_script[-300:]}")
    parts.append(f"\nSlide title: {slide['title']}")
    if slide.get("bullets"):
        parts.append("Slide text:\n- " + "\n- ".join(slide["bullets"][:40]))
    parts.append("Speaker notes:\n" + (slide.get("notes") or "(none - build the narration from the slide text)"))
    parts.append("\nWrite the spoken script now.")
    return "\n".join(parts)


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^\s*(script|narration)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"[*_#`>]+", "", text)
    # strip stage directions: [pause], (beat), *smiles*, or a bare "Pause" between sentences
    text = re.sub(r"[\[(]\s*(pause|beat|smiles?|laughs?|breathe|silence)[^\])]*[\])]", " ", text, flags=re.I)
    text = re.sub(r"(?:^|(?<=[.!?—:]))\s*(?:Pause|Beat|Silence)\b[.…]*(?=\s|$)", "", text, flags=re.M)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip().strip('"').strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _fallback_script(slide: dict, total: int, prev_title: str | None) -> str:
    """No-LLM path: tidy up the notes into flowing sentences with light transitions."""
    source = slide.get("notes") or ". ".join(slide.get("bullets") or [])
    lines = [ln.strip(" \t-•*–·") for ln in source.splitlines()]
    sentences = []
    for ln in lines:
        if not ln:
            continue
        ln = ln[0].upper() + ln[1:]
        if ln[-1] not in ".!?:;":
            ln += "."
        sentences.append(ln)
    body = " ".join(sentences) or f"Let us turn our attention to {slide['title']}."
    if slide["index"] == 1:
        intro = "Good day, everyone, and thank you for being here. "
    elif prev_title:
        intro = f"Having considered {prev_title.rstrip('.')}, let us now turn to {slide['title'].rstrip('.')}. "
    else:
        intro = ""
    outro = " Thank you." if slide["index"] == total else ""
    return intro + body + outro


def write_script(slide: dict, total: int, prefs: dict, prev_title: str | None = None,
                 prev_script: str | None = None) -> tuple[str, str]:
    """Return (script, engine) where engine is 'llm' or 'local'."""
    if not llm_enabled():
        return _fallback_script(slide, total, prev_title), "local"
    text = llm.chat(SYSTEM_PROMPT, _build_user_prompt(slide, total, prefs, prev_title, prev_script),
                    max_tokens=None)
    return _clean(text), "llm"
