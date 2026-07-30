import os
import json
import uuid

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, abort

import pptx_render
import doc_parse
import audio_analysis
import claude_analysis

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

# In-memory session store -- fine for a local single-user demo.
SESSIONS: dict[str, dict] = {}


def _session_dir(session_id: str) -> str:
    path = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(path, exist_ok=True)
    return path


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "pptx" not in request.files:
        return jsonify({"error": "A PPTX file is required."}), 400

    session_id = uuid.uuid4().hex
    sdir = _session_dir(session_id)

    pptx_file = request.files["pptx"]
    pptx_path = os.path.join(sdir, "deck.pptx")
    pptx_file.save(pptx_path)

    slides = pptx_render.extract_slide_text(pptx_path)
    slide_img_dir = os.path.join(sdir, "slides")
    image_paths = pptx_render.render_slides_to_png(pptx_path, slide_img_dir)
    slide_images = {i: p for i, p in enumerate(image_paths)}

    xlsx_summary = ""
    if "xlsx" in request.files and request.files["xlsx"].filename:
        xlsx_file = request.files["xlsx"]
        xlsx_path = os.path.join(sdir, "model.xlsx")
        xlsx_file.save(xlsx_path)
        xlsx_summary = doc_parse.summarize_xlsx(xlsx_path)

    context_texts = []
    for f in request.files.getlist("context"):
        if not f.filename:
            continue
        ctx_path = os.path.join(sdir, f.filename)
        f.save(ctx_path)
        try:
            context_texts.append(doc_parse.extract_context_doc(ctx_path))
        except Exception as e:
            context_texts.append(f"(failed to parse {f.filename}: {e})")

    SESSIONS[session_id] = {
        "slides": slides,
        "slide_images": slide_images,
        "xlsx_summary": xlsx_summary,
        "context_texts": context_texts,
        "audio_result": None,
    }

    return jsonify({
        "session_id": session_id,
        "slides": [
            {"index": s["index"], "title": s["title"]} for s in slides
        ],
    })


@app.route("/api/slide_image/<session_id>/<int:index>")
def slide_image(session_id, index):
    session = SESSIONS.get(session_id)
    if not session:
        abort(404)
    path = session["slide_images"].get(index)
    if not path or not os.path.exists(path):
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


@app.route("/api/upload_audio/<session_id>", methods=["POST"])
def upload_audio(session_id):
    session = SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "Unknown session"}), 404

    if "audio" not in request.files:
        return jsonify({"error": "An audio file is required."}), 400

    sdir = _session_dir(session_id)
    audio_file = request.files["audio"]
    audio_path = os.path.join(sdir, "recording.webm")
    audio_file.save(audio_path)

    timestamps = json.loads(request.form.get("timestamps", "[]"))

    result = audio_analysis.analyze(audio_path, timestamps)
    session["audio_result"] = result

    return jsonify(result)


@app.route("/api/analyze/<session_id>", methods=["POST"])
def analyze(session_id):
    session = SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "Unknown session"}), 404

    result = claude_analysis.analyze(
        slides=session["slides"],
        slide_image_paths=session["slide_images"],
        xlsx_summary=session["xlsx_summary"],
        context_texts=session["context_texts"],
        audio_result=session["audio_result"],
    )
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
