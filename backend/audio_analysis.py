"""Transcribe presentation audio and compute quantitative delivery metrics.

Audio is decoded once (via faster-whisper's bundled PyAV-based decoder) into a mono
float32 array, then reused for both transcription and librosa feature extraction --
avoids relying on soundfile/libsndfile for webm/opus, which it doesn't reliably support.
"""
import re
from functools import lru_cache

import truststore
truststore.inject_into_ssl()  # trust the OS cert store (needed behind corporate TLS-inspecting proxies)

import librosa
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

SR = 16000
PITCH_HOP_LENGTH = 512
FILLER_WORDS = {"um", "uh", "umm", "uhh", "like", "you know", "sort of", "kind of", "basically", "actually"}


@lru_cache(maxsize=1)
def _get_model() -> WhisperModel:
    return WhisperModel("base.en", device="cpu", compute_type="int8")


def transcribe(audio_path: str) -> dict:
    """Decode once, transcribe with word-level timestamps. Returns audio array + transcript data."""
    audio = decode_audio(audio_path, sampling_rate=SR)
    model = _get_model()
    segments, info = model.transcribe(
        audio, word_timestamps=True, vad_filter=True, language="en"
    )
    words = []
    full_segments = []
    for seg in segments:
        full_segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        if seg.words:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    duration = len(audio) / SR
    return {
        "audio": audio,
        "duration": duration,
        "segments": full_segments,
        "words": words,
    }


def _count_fillers(text: str) -> int:
    text = text.lower()
    count = 0
    for filler in FILLER_WORDS:
        count += len(re.findall(rf"\b{re.escape(filler)}\b", text))
    return count


def _pitch_and_volume_curves(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run pitch + volume analysis once for the whole track. Callers slice the result by
    time range instead of each re-running pyin on their own sub-clip -- pyin is the
    expensive part, and running it per-slide plus once more for "overall" was redundant."""
    f0, _, _ = librosa.pyin(
        audio, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
        sr=SR, hop_length=PITCH_HOP_LENGTH,
    )
    rms = librosa.feature.rms(y=audio, hop_length=PITCH_HOP_LENGTH)[0]
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    times = librosa.times_like(f0, sr=SR, hop_length=PITCH_HOP_LENGTH)
    return times, f0, rms


def _delivery_metrics_for_range(words: list[dict], pauses: list[tuple],
                                 curve_times: np.ndarray, f0: np.ndarray, rms: np.ndarray,
                                 start: float, end: float) -> dict:
    span_words = [w for w in words if start <= w["start"] < end]
    text = " ".join(w["word"] for w in span_words).strip()
    duration_min = max((end - start) / 60.0, 1e-6)
    wpm = len(span_words) / duration_min

    frame_mask = (curve_times >= start) & (curve_times < end)

    rms_slice = rms[frame_mask]
    avg_rms = float(np.mean(rms_slice)) if rms_slice.size else 0.0

    voiced = f0[frame_mask]
    voiced = voiced[~np.isnan(voiced)]
    avg_pitch = float(np.mean(voiced)) if voiced.size else 0.0
    pitch_std = float(np.std(voiced)) if voiced.size else 0.0

    span_pauses = [p for p in pauses if start <= p[0] < end]
    pause_count = len(span_pauses)
    total_pause = sum(p[1] - p[0] for p in span_pauses)
    longest_pause = max((p[1] - p[0] for p in span_pauses), default=0.0)

    return {
        "transcript": text,
        "words_per_minute": round(wpm, 1),
        "filler_word_count": _count_fillers(text),
        "avg_volume_rms": round(avg_rms, 4),
        "avg_pitch_hz": round(avg_pitch, 1),
        "pitch_variation_hz": round(pitch_std, 1),
        "pause_count": pause_count,
        "total_pause_seconds": round(total_pause, 1),
        "longest_pause_seconds": round(longest_pause, 1),
    }


def compute_pauses(audio: np.ndarray, top_db: int = 30) -> list[tuple]:
    """Return list of (start_sec, end_sec) silence gaps between non-silent intervals."""
    intervals = librosa.effects.split(audio, top_db=top_db)
    pauses = []
    for i in range(len(intervals) - 1):
        gap_start = intervals[i][1] / SR
        gap_end = intervals[i + 1][0] / SR
        if gap_end - gap_start > 0.3:
            pauses.append((gap_start, gap_end))
    return pauses


def analyze(audio_path: str, slide_timestamps: list[dict]) -> dict:
    """slide_timestamps: [{slideIndex, timestamp}] marking when each slide became active.

    Returns overall + per-slide transcript and delivery metrics.
    """
    data = transcribe(audio_path)
    audio, duration, words = data["audio"], data["duration"], data["words"]
    pauses = compute_pauses(audio)
    curve_times, f0, rms = _pitch_and_volume_curves(audio)

    marks = sorted(slide_timestamps, key=lambda m: m["timestamp"])
    if not marks:
        marks = [{"slideIndex": 0, "timestamp": 0.0}]

    per_slide = []
    for i, mark in enumerate(marks):
        start = mark["timestamp"]
        end = marks[i + 1]["timestamp"] if i + 1 < len(marks) else duration
        metrics = _delivery_metrics_for_range(words, pauses, curve_times, f0, rms, start, end)
        per_slide.append({"slide_index": mark["slideIndex"], "start": round(start, 1),
                           "end": round(end, 1), **metrics})

    overall = _delivery_metrics_for_range(words, pauses, curve_times, f0, rms, 0.0, duration)
    return {"duration": round(duration, 1), "overall": overall, "per_slide": per_slide}
