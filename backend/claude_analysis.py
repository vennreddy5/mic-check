"""Build the Claude API call that ties together deck content, source data, transcript,
and delivery metrics into one structured critique: does the deck tie to its sources,
does the talk track match the deck, and how was it delivered.
"""
import base64
import json
import os
import re

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a meticulous, skeptical engagement manager reviewing a client \
deliverable and a rehearsal recording of a colleague presenting it, the night before the \
real client meeting. You care about three things:

1. SOURCE CONSISTENCY: does every number/claim in the deck actually tie back to the \
   supporting Excel model and context documents? Flag anything unsupported, contradicted, \
   or that doesn't match the source data.
2. NARRATIVE QUALITY: does each slide's headline follow from its content, is the "so what" \
   clear, and does the story arc hold together end to end across the whole deck?
3. TALK TRACK FIDELITY: does what the presenter actually SAID match the slide content and \
   the deck's narrative? Flag misstatements, invented numbers not on the slide or in the \
   source data, and places where the spoken narrative drifts from the deck's story.

You also have quantitative delivery metrics (pace, pauses, filler words, pitch/volume) for \
each slide's talk track -- use them to add delivery notes, but only where they connect to \
something substantive (e.g. "rushed pace on the slide with your weakest source support"), \
not generic pacing commentary for its own sake.

Respond with ONLY a single JSON object, no prose before or after, matching this shape:
{
  "overall_summary": "2-4 sentence overall read of the deliverable + rehearsal",
  "narrative_arc_assessment": "does the deck's story hold together end to end",
  "per_slide": [
    {
      "slide_index": 0,
      "consistency_flags": [
        {"claim": "...", "issue": "...", "severity": "low|medium|high", "source": "deck|talk_track"}
      ],
      "narrative_notes": "does this slide's headline follow from its content, is the so-what clear",
      "talk_track_notes": "does the spoken content on this slide match the deck and hold up factually",
      "delivery_notes": "only if genuinely notable, tied to a specific content moment"
    }
  ]
}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            snippet = match.group(0)[max(0, exc.pos - 60): exc.pos + 60]
            raise ValueError(
                f"Claude returned malformed JSON (possibly truncated — try increasing max_tokens). "
                f"Parse error at char {exc.pos}: {exc.msg}. Near: …{snippet}…"
            ) from exc
    raise ValueError(f"Could not parse JSON from Claude response: {text[:500]}")


def _build_user_content(slides, slide_image_paths, xlsx_summary, context_texts, audio_result):
    content = []

    content.append({"type": "text", "text": "## SUPPORTING SOURCE DATA (Excel model)\n" +
                     (xlsx_summary or "(none provided)")})

    if context_texts:
        joined = "\n\n---\n\n".join(context_texts)
        content.append({"type": "text", "text": "## CONTEXT DOCUMENTS\n" + joined})

    content.append({"type": "text", "text": "## DECK CONTENT, SLIDE BY SLIDE (with talk track + delivery metrics)"})

    per_slide_audio = {s["slide_index"]: s for s in (audio_result or {}).get("per_slide", [])}

    for slide in slides:
        idx = slide["index"]
        header = f"\n### Slide {idx}: {slide['title'] or '(no title)'}"
        body = f"Body text:\n{slide['body'] or '(none)'}"
        notes = f"Speaker notes:\n{slide['notes']}" if slide["notes"] else ""

        audio = per_slide_audio.get(idx)
        if audio:
            talk = (
                f"Talk track (spoken): \"{audio['transcript']}\"\n"
                f"Delivery metrics: {audio['words_per_minute']} wpm, "
                f"{audio['filler_word_count']} filler words, "
                f"{audio['pause_count']} pauses ({audio['total_pause_seconds']}s total), "
                f"avg pitch {audio['avg_pitch_hz']}Hz (variation {audio['pitch_variation_hz']}Hz)"
            )
        else:
            talk = "Talk track (spoken): (no audio for this slide)"

        content.append({"type": "text", "text": "\n".join([header, body, notes, talk])})

        img_path = slide_image_paths.get(idx)
        if img_path and os.path.exists(img_path):
            with open(img_path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })

    content.append({"type": "text", "text": (
        "\nNow produce the JSON critique described in the system prompt. "
        "Be specific and cite slide numbers. Do not invent issues that aren't there."
    )})
    return content


def analyze(slides, slide_image_paths, xlsx_summary, context_texts, audio_result) -> dict:
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    user_content = _build_user_content(slides, slide_image_paths, xlsx_summary, context_texts, audio_result)

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(text)
