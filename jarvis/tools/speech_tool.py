import json
import mimetypes
import uuid
from pathlib import Path
from urllib import request

from jarvis.agents.llm import client
from jarvis.config import STT_PROVIDER, STT_SERVER_TIMEOUT, STT_SERVER_TOKEN, STT_SERVER_URL


class SpeechTranscriptionError(RuntimeError):
    pass


class SpeechQuotaError(SpeechTranscriptionError):
    pass


def _is_quota_error(exc: Exception) -> bool:
    text = repr(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    status_code = getattr(exc, "status_code", None)
    return status_code == 429 or "insufficient_quota" in code or "insufficient_quota" in text


def _transcribe_openai(path: Path, language: str) -> str:
    try:
        with path.open("rb") as audio_file:
            result = client().audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
                response_format="text",
            )
    except Exception as exc:
        if _is_quota_error(exc):
            raise SpeechQuotaError("OpenAI API quota is exhausted") from exc
        raise SpeechTranscriptionError(f"Speech transcription failed: {exc.__class__.__name__}") from exc
    return str(result).strip()


def _multipart_body(path: Path, language: str, boundary: str) -> bytes:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"{language}\r\n",
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n",
    ]
    body = b"".join(part.encode("utf-8") for part in parts)
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body


def _transcribe_server(path: Path, language: str) -> str:
    if not STT_SERVER_URL:
        raise SpeechTranscriptionError("STT_SERVER_URL is not configured")

    boundary = f"jarvis-{uuid.uuid4().hex}"
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if STT_SERVER_TOKEN:
        headers["Authorization"] = f"Bearer {STT_SERVER_TOKEN}"
    body = _multipart_body(path, language, boundary)
    req = request.Request(STT_SERVER_URL, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=STT_SERVER_TIMEOUT) as resp:
            payload = resp.read().decode("utf-8")
    except Exception as exc:
        raise SpeechTranscriptionError(f"Speech server request failed: {exc.__class__.__name__}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SpeechTranscriptionError("Speech server returned invalid JSON") from exc
    text = str(data.get("text", "")).strip()
    if not text:
        raise SpeechTranscriptionError("Speech server returned empty text")
    return text


def transcribe_audio(path: Path, language: str = "ru") -> str:
    if STT_PROVIDER == "server":
        return _transcribe_server(path, language)
    if STT_PROVIDER != "openai":
        raise SpeechTranscriptionError(f"Unknown STT_PROVIDER: {STT_PROVIDER}")
    return _transcribe_openai(path, language)
