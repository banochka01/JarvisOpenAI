import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from faster_whisper import WhisperModel


MODEL_NAME = os.getenv("STT_MODEL", "small")
DEVICE = os.getenv("STT_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
STT_TOKEN = os.getenv("STT_TOKEN", "")

app = FastAPI(title="Jarvis STT Server")
model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global model
    if model is None:
        model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    return model


def check_auth(authorization: str | None):
    if not STT_TOKEN:
        return
    expected = f"Bearer {STT_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME, "device": DEVICE}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ru"),
    authorization: str | None = Header(default=None),
):
    check_auth(authorization)
    suffix = Path(file.filename or "voice.ogg").suffix or ".ogg"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="jarvis_stt_", suffix=suffix, delete=False) as tmp:
            temp_path = Path(tmp.name)
            tmp.write(await file.read())

        segments, info = get_model().transcribe(str(temp_path), language=language, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return {"text": text, "language": info.language}
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
