"""Audio validation, STT/TTS provider behaviour, and the voice/avatar HTTP pipeline."""
from __future__ import annotations

import base64

import pytest

from app.errors import ServiceUnavailable
from app.speech.audio_utils import AudioError, sniff_format, validate_audio
from app.speech.providers import (
    SpeechService, TranscriptResult, UnavailableSTT, UnavailableTTS, build_stt, build_tts, prepare_tts_text,
)
from tests.conftest import SESSION, make_wav


def upload(client, data: bytes, name="rec.wav", ctype="audio/wav", **form):
    return client.post("/api/v1/audio/process", files={"file": (name, data, ctype)}, data={"session_id": SESSION, **form})


# ------------------------------------------------------------------ validation (no speech libs needed)
def test_sniff_formats():
    assert sniff_format(make_wav()) == "wav"
    assert sniff_format(b"OggS" + b"\0" * 20) == "ogg" and sniff_format(b"\x1a\x45\xdf\xa3" + b"\0" * 20) == "webm"
    assert sniff_format(b"ID3\x03" + b"\0" * 20) == "mp3" and sniff_format(b"\0\0\0\x20ftypM4A " + b"\0" * 8) == "m4a"
    assert sniff_format(b"MZ\x90\x00 executable") is None


@pytest.mark.parametrize("data,code", [
    (b"", "empty_audio"),
    (b"this is plainly text", "unsupported_audio_format"),
    (b"RIFF\x00\x00\x00\x00WAVEjunk", "invalid_audio"),
    (make_wav(0.1), "audio_too_short"),
    (make_wav(70), "audio_too_long"),
    (make_wav(1.0, amp=0.0), "no_speech"),
])
def test_validate_rejects_bad_audio(settings, data, code):
    with pytest.raises(Exception) as e:
        validate_audio(data, settings)
    assert e.value.code == code


def test_validate_accepts_good_wav_and_oversize_rejected(make_settings):
    info = validate_audio(make_wav(1.5), make_settings())
    assert info.format == "wav" and 1.4 < info.duration_s < 1.6 and info.rms > 0.1
    with pytest.raises(Exception) as e:
        validate_audio(make_wav(2.0), make_settings(max_audio_mb=0.01))
    assert e.value.code == "audio_too_large"


# ------------------------------------------------------------------ provider availability
def test_providers_report_missing_dependencies(make_settings):
    stt = build_stt(make_settings(stt_provider="faster-whisper"))
    assert isinstance(stt, UnavailableSTT) or stt.available  # faster-whisper not installed in CI -> explains why
    if isinstance(stt, UnavailableSTT):
        assert "faster-whisper" in stt.reason
    assert isinstance(build_stt(make_settings(stt_provider="none")), UnavailableSTT)
    tts = build_tts(make_settings(tts_provider="piper"))
    assert isinstance(tts, UnavailableTTS) and "PIPER_MODEL_PATH" in tts.reason
    with pytest.raises(ServiceUnavailable):
        tts.synthesize_sync("hello")


def test_tts_text_preparation():
    assert prepare_tts_text("**Bold** [1] see https://x.io/page now\n- item", 500) == "Bold see now item"
    long = "First sentence here. " * 60
    out = prepare_tts_text(long, 100)
    assert len(out) <= 100 and out.endswith(".")


async def test_speech_service_runs_off_event_loop_and_times_out(make_settings):
    import time
    from tests.conftest import FakeSTT, FakeTTS

    class Slow(FakeSTT):
        def transcribe_sync(self, data, fmt):
            time.sleep(0.5)
            return self.result

    svc = SpeechService(make_settings(stt_timeout_s=0.05), Slow(), FakeTTS())
    with pytest.raises(ServiceUnavailable) as e:
        await svc.transcribe(b"x", "wav")
    assert e.value.code == "stt_timeout"
    ok = SpeechService(make_settings(), FakeSTT(), FakeTTS())
    assert (await ok.transcribe(b"x", "wav")).text
    with pytest.raises(ServiceUnavailable):
        await ok.synthesize("   ")


# ------------------------------------------------------------------ /audio/transcribe
def test_transcribe_endpoint(client):
    r = client.post("/api/v1/audio/transcribe", files={"file": ("a.wav", make_wav(1.2), "audio/wav")}, data={"session_id": SESSION})
    b = r.json()
    assert r.status_code == 200 and b["transcript"] == "what services do you offer" and b["low_confidence"] is False


@pytest.mark.parametrize("data,status,code", [
    (b"", 422, "empty_audio"), (b"hello world", 415, "unsupported_audio_format"), (make_wav(0.05), 422, "audio_too_short"),
])
def test_transcribe_rejections(client, data, status, code):
    r = client.post("/api/v1/audio/transcribe", files={"file": ("a.wav", data, "audio/wav")}, data={"session_id": SESSION})
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_upload_larger_than_limit_is_413(make_settings, fake_llm, fake_stt, ingested):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.rag.embeddings import HashingEmbedder
    app = create_app(make_settings(max_audio_mb=0.05), llm=fake_llm, embedder=HashingEmbedder(), stt=fake_stt)
    with TestClient(app) as c:
        r = c.post("/api/v1/audio/transcribe", files={"file": ("a.wav", make_wav(5), "audio/wav")}, data={"session_id": SESSION})
    assert r.status_code == 413


def test_missing_session_id_rejected(client):
    r = client.post("/api/v1/audio/transcribe", files={"file": ("a.wav", make_wav(), "audio/wav")}, data={"session_id": "bad"})
    assert r.status_code == 422


def test_stt_unavailable_is_503_with_actionable_message(client, fake_stt):
    fake_stt.error = ServiceUnavailable("Voice input is not available right now. You can type your question instead.", code="stt_unavailable")
    r = upload(client, make_wav())
    assert r.status_code == 503 and "type your question" in r.json()["error"]["message"]


def test_recognition_failure_returns_422(client, fake_stt):
    fake_stt.error = AudioError("I couldn't make out any speech.", code="no_speech")
    r = upload(client, make_wav())
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_speech"


# ------------------------------------------------------------------ /audio/process (voice + avatar share the engine)
def test_voice_turn_end_to_end(client, fake_tts):
    fake_tts_text = fake_tts
    r = upload(client, make_wav(1.5), interaction_mode="voice")
    b = r.json()
    assert r.status_code == 200 and b["transcript"] == "what services do you offer"
    assert b["chat"]["interaction_mode"] == "voice" and b["chat"]["answer"]
    assert base64.b64decode(b["audio_base64"]).startswith(b"RIFF") and b["audio_mime"] == "audio/wav" and b["tts_error"] is None
    assert fake_tts_text.last_text and "[1]" not in fake_tts_text.last_text


def test_voice_and_text_share_one_conversation(client):
    t = client.post("/api/v1/chat", json={"session_id": SESSION, "message": "Do you build chatbots?"}).json()
    v = upload(client, make_wav(), conversation_id=t["conversation_id"], interaction_mode="avatar").json()
    assert v["chat"]["conversation_id"] == t["conversation_id"]
    turns = client.get(f"/api/v1/conversations/{t['conversation_id']}", headers={"X-Session-ID": SESSION}).json()["turns"]
    assert [x["mode"] for x in turns] == ["text", "text", "avatar", "avatar"]


def test_tts_failure_still_returns_text_answer(client, fake_tts):
    fake_tts.error = ServiceUnavailable("nope", code="tts_failed")
    r = upload(client, make_wav())
    b = r.json()
    assert r.status_code == 200 and b["chat"]["answer"] and b["audio_base64"] is None and b["tts_error"] == "tts_failed"


def test_with_audio_false_skips_tts(client, fake_tts):
    b = upload(client, make_wav(), with_audio="false").json()
    assert b["audio_base64"] is None and fake_tts.last_text == ""


def test_low_confidence_transcript_asks_for_confirmation_without_calling_llm(client, fake_stt, fake_llm):
    fake_stt.result = TranscriptResult("wat serv ices", "en", 1.0, 0.1)
    b = upload(client, make_wav()).json()
    assert b["needs_confirmation"] and b["low_confidence"] and b["chat"] is None and b["transcript"] == "wat serv ices"
    assert fake_llm.calls == []
    # the user edits the transcript and sends it through the normal text endpoint (same engine)
    fixed = client.post("/api/v1/chat", json={"session_id": SESSION, "message": "what services do you offer",
                                              "interaction_mode": "voice", "transcript_confidence": 0.1}).json()
    assert fixed["interaction_mode"] == "voice" and fixed["answer"]


def test_confirmation_can_be_disabled(client, fake_stt):
    fake_stt.result = TranscriptResult("wat serv ices", "en", 1.0, 0.1)
    assert upload(client, make_wav(), confirm_low_confidence="false").json()["chat"] is not None


def test_interrupted_flag_flows_to_engine(client):
    a = upload(client, make_wav(), interaction_mode="avatar").json()
    upload(client, make_wav(), conversation_id=a["chat"]["conversation_id"], interaction_mode="avatar", interrupted_previous="true")
    conv = client.app_state.container.sessions.find_conversation(SESSION, a["chat"]["conversation_id"])
    assert conv.turns[1].audio_interrupted


def test_duplicate_voice_submission_is_idempotent(client, fake_llm):
    a = upload(client, make_wav(), client_message_id="msg_voice_01").json()
    n = len(fake_llm.calls)
    b = upload(client, make_wav(), conversation_id=a["chat"]["conversation_id"], client_message_id="msg_voice_01").json()
    assert b["chat"]["duplicate"] and len(fake_llm.calls) == n


def test_invalid_mode_for_audio_rejected(client):
    assert upload(client, make_wav(), interaction_mode="text").status_code == 422


# ------------------------------------------------------------------ /audio/synthesize
def test_synthesize_endpoint(client, fake_tts):
    r = client.post("/api/v1/audio/synthesize", json={"session_id": SESSION, "text": "Hello **there** [1]"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content.startswith(b"RIFF")
    assert fake_tts.last_text == "Hello there"
    assert r.headers["cache-control"] == "no-store"


def test_synthesize_validation_and_failure(client, fake_tts):
    assert client.post("/api/v1/audio/synthesize", json={"session_id": SESSION, "text": ""}).status_code == 422
    assert client.post("/api/v1/audio/synthesize", json={"session_id": SESSION, "text": "x" * 5000}).status_code == 422
    fake_tts.error = ServiceUnavailable("x", code="tts_failed")
    assert client.post("/api/v1/audio/synthesize", json={"session_id": SESSION, "text": "hi"}).status_code == 503


# ------------------------------------------------------------------ Piper subprocess contract
def _fake_piper(tmp_path, *, exit_code=0, sleep=0.0, write=True):
    """A stand-in for the piper CLI: parses the same flags we pass, reads stdin, writes a WAV."""
    import stat, sys, textwrap
    if sys.platform == "win32":
        py_script = tmp_path / "fake_piper.py"
        py_script.write_text(textwrap.dedent(f'''\
            import argparse, sys, time, wave
            ap = argparse.ArgumentParser()
            ap.add_argument("--model", required=True); ap.add_argument("--output_file", required=True)
            a = ap.parse_args()
            text = sys.stdin.read()
            time.sleep({sleep})
            if {exit_code}: sys.exit({exit_code})
            if {write}:
                with wave.open(a.output_file, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\\x01\\x00" * (16000 // 2 + len(text)))
            '''))
        bat_script = tmp_path / "fake_piper.bat"
        bat_script.write_text(f'@echo off\n"{sys.executable}" "{py_script}" %*\n')
        script = bat_script
    else:
        script = tmp_path / "fake_piper.py"
        script.write_text(textwrap.dedent(f'''\
            #!{sys.executable}
            import argparse, sys, time, wave
            ap = argparse.ArgumentParser()
            ap.add_argument("--model", required=True); ap.add_argument("--output_file", required=True)
            a = ap.parse_args()
            text = sys.stdin.read()
            time.sleep({sleep})
            if {exit_code}: sys.exit({exit_code})
            if {write}:
                with wave.open(a.output_file, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\\x01\\x00" * (16000 // 2 + len(text)))
            '''))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"x")
    return script, model


def test_piper_provider_invokes_cli_and_returns_wav(tmp_path, make_settings):
    from app.speech.providers import PiperTTS
    script, model = _fake_piper(tmp_path)
    tts = build_tts(make_settings(piper_binary=str(script), piper_model_path=str(model)))
    assert isinstance(tts, PiperTTS)
    audio, mime = tts.synthesize_sync("Hello from the test")
    assert mime == "audio/wav" and audio[:4] == b"RIFF" and len(audio) > 1000
    import glob, tempfile
    assert not [f for f in glob.glob(tempfile.gettempdir() + "/tmp*.wav") if f.endswith(".wav") and "piper" in f]  # no leaked temp files


@pytest.mark.parametrize("kw,code", [({"exit_code": 3}, "tts_failed"), ({"write": False}, "tts_failed")])
def test_piper_failures_become_safe_errors(tmp_path, make_settings, kw, code):
    script, model = _fake_piper(tmp_path, **kw)
    tts = build_tts(make_settings(piper_binary=str(script), piper_model_path=str(model)))
    with pytest.raises(ServiceUnavailable) as e:
        tts.synthesize_sync("hello")
    assert e.value.code == code


def test_piper_timeout(tmp_path, make_settings):
    script, model = _fake_piper(tmp_path, sleep=2.0)
    tts = build_tts(make_settings(piper_binary=str(script), piper_model_path=str(model), tts_timeout_s=0.3))
    with pytest.raises(ServiceUnavailable) as e:
        tts.synthesize_sync("hello")
    assert e.value.code == "tts_timeout"
