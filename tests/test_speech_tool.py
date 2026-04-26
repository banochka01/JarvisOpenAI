from pathlib import Path
from urllib import error

from jarvis.tools import speech_tool


def test_transcribe_audio_uses_openai_audio_api(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")
    calls = []

    class FakeTranscriptions:
        def create(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["file"].read() == b"fake audio"
            return "привет"

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "openai")
    monkeypatch.setattr(speech_tool, "client", lambda: FakeClient())

    assert speech_tool.transcribe_audio(Path(audio_path)) == "привет"
    assert calls[0]["model"] == "whisper-1"
    assert calls[0]["language"] == "ru"
    assert calls[0]["response_format"] == "text"


def test_transcribe_audio_turns_quota_error_into_specific_exception(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")

    class FakeQuotaError(Exception):
        status_code = 429
        code = "insufficient_quota"

    class FakeTranscriptions:
        def create(self, **kwargs):
            raise FakeQuotaError("quota")

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "openai")
    monkeypatch.setattr(speech_tool, "client", lambda: FakeClient())

    try:
        speech_tool.transcribe_audio(audio_path)
    except speech_tool.SpeechQuotaError as exc:
        assert "quota" in str(exc).lower()
    else:
        raise AssertionError("SpeechQuotaError was not raised")


def test_transcribe_audio_hides_raw_provider_errors(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")

    class FakeTranscriptions:
        def create(self, **kwargs):
            raise RuntimeError("secret provider details")

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "openai")
    monkeypatch.setattr(speech_tool, "client", lambda: FakeClient())

    try:
        speech_tool.transcribe_audio(audio_path)
    except speech_tool.SpeechTranscriptionError as exc:
        assert "secret provider details" not in str(exc)
    else:
        raise AssertionError("SpeechTranscriptionError was not raised")


def test_transcribe_audio_can_use_stt_server(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return '{"text":"привет с сервера"}'.encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "server")
    monkeypatch.setattr(speech_tool, "STT_SERVER_URL", "https://stt.example.test/transcribe")
    monkeypatch.setattr(speech_tool, "STT_SERVER_TOKEN", "secret")
    monkeypatch.setattr(speech_tool, "STT_SERVER_TIMEOUT", 42)
    monkeypatch.setattr(speech_tool.request, "urlopen", fake_urlopen)

    text = speech_tool.transcribe_audio(audio_path)

    assert text == "привет с сервера"
    assert captured["url"] == "https://stt.example.test/transcribe"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert "multipart/form-data" in captured["headers"]["Content-type"]
    assert b'name="language"' in captured["body"]
    assert b"fake audio" in captured["body"]
    assert captured["timeout"] == 42


def test_transcribe_audio_requires_server_url(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "server")
    monkeypatch.setattr(speech_tool, "STT_SERVER_URL", "")

    try:
        speech_tool.transcribe_audio(audio_path)
    except speech_tool.SpeechTranscriptionError as exc:
        assert "STT_SERVER_URL" in str(exc)
    else:
        raise AssertionError("SpeechTranscriptionError was not raised")


def test_transcribe_audio_hides_server_network_details(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio")

    def fake_urlopen(req, timeout):
        raise error.URLError("private network details")

    monkeypatch.setattr(speech_tool, "STT_PROVIDER", "server")
    monkeypatch.setattr(speech_tool, "STT_SERVER_URL", "https://stt.example.test/transcribe")
    monkeypatch.setattr(speech_tool.request, "urlopen", fake_urlopen)

    try:
        speech_tool.transcribe_audio(audio_path)
    except speech_tool.SpeechTranscriptionError as exc:
        assert "private network details" not in str(exc)
    else:
        raise AssertionError("SpeechTranscriptionError was not raised")
