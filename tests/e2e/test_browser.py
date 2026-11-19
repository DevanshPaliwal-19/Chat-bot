"""Real-browser end-to-end tests (headless Chromium + fake microphone) against the real backend and Streamlit app.

Skipped automatically when Playwright/Chromium aren't installed. Run with:  pytest tests/e2e -m e2e -s
What is real: FastAPI app, Streamlit UI, the custom voice/avatar component, OllamaProvider (over HTTP to a mock).
What is stand-in: the LLM (scripts/mock_ollama.py), STT (fixed transcript) and TTS (a tone).
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SHOTS = Path(os.environ.get("E2E_SHOTS", ROOT / "data" / "e2e_shots"))
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"{url} did not start")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    data = tmp_path_factory.mktemp("e2e_data")
    ollama, backend, ui = _free_port(), _free_port(), _free_port()
    env = {**os.environ, "E2E_DATA_DIR": str(data), "E2E_OLLAMA_URL": f"http://127.0.0.1:{ollama}", "E2E_BACKEND_PORT": str(backend),
           "E2E_CORS": f"http://127.0.0.1:{ui},http://localhost:{ui}", "PYTHONPATH": str(ROOT),
           "OSIYA_BACKEND_URL": f"http://127.0.0.1:{backend}", "OSIYA_COMPANY_NAME": "Fixture Corp", "OSIYA_ASSISTANT_NAME": "Fixie"}
    procs = {
        "ollama": subprocess.Popen([sys.executable, "scripts/mock_ollama.py", "--port", str(ollama)], cwd=ROOT, env=env),
        "backend": subprocess.Popen([sys.executable, "tests/e2e/server.py"], cwd=ROOT, env=env),
        "ui": subprocess.Popen([sys.executable, "-m", "streamlit", "run", "frontend/app.py", "--server.port", str(ui), "--server.headless", "true",
                                "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    }
    try:
        _wait(f"http://127.0.0.1:{ollama}/api/tags")
        _wait(f"http://127.0.0.1:{backend}/health")
        _wait(f"http://127.0.0.1:{ui}/_stcore/health")
        yield {"ui": f"http://127.0.0.1:{ui}", "backend": f"http://127.0.0.1:{backend}", "data": data, "procs": procs}
    finally:
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                        "--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:  # no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, stack):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, permissions=["microphone"])
    pg = ctx.new_page()
    pg.set_default_timeout(25000)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(stack["ui"])
    pg.get_by_text("Welcome to Fixture Corp").wait_for()
    pg.js_errors = errors  # type: ignore[attr-defined]
    pg.baseline = {c["id"] for c in all_convs(stack)}  # type: ignore[attr-defined]
    yield pg
    ctx.close()


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def send_text(page, text: str) -> None:
    box = page.get_by_test_id("stChatInputTextArea")
    box.fill(text)
    box.press("Enter")


def messages(page):
    return page.locator('[data-testid="stChatMessage"]')


def wait_answer(page, count: int) -> None:
    page.wait_for_function("n => document.querySelectorAll('[data-testid=\"stChatMessage\"]').length >= n", arg=count)
    page.locator(".osiya-typing").wait_for(state="detached")


def voice_frame(page):
    return page.frame_locator("iframe[title*='osiya_voice_avatar']")


def select_mode(page, label: str) -> None:
    page.get_by_role("radio", name=re.compile(label)).click()  # st.segmented_control items are role=radio
    voice_frame(page).locator("#mic").wait_for()
    assert page.locator("iframe[title*='osiya_voice_avatar']").evaluate("e => e.getBoundingClientRect().height") < 700  # regression: iframe height feedback loop


def all_convs(stack):
    sessions = json.load(urllib.request.urlopen(f"{stack['backend']}/__debug/sessions"))["sessions"]
    out = []
    for s in sessions:
        out += json.load(urllib.request.urlopen(f"{stack['backend']}/__debug/conversation/{s}"))["conversations"]
    return out


def backend_conv(stack, page):
    """Conversations created since this test's page was opened (the stack is shared across tests)."""
    return [c for c in all_convs(stack) if c["id"] not in page.baseline]


# ------------------------------------------------------------------ tests
def test_page_loads_with_branding_and_no_js_errors(page):
    assert page.get_by_text("Fixture Corp AI Assistant").is_visible()
    assert page.locator(".osiya-hero").is_visible() and "Online" in page.locator(".osiya-hero").inner_text()
    assert page.get_by_role("button", name="What services do you offer?").is_visible()
    shot(page, "01_home")
    assert page.js_errors == []


def test_text_chat_in_browser(page, stack):
    send_text(page, "Can you build an iOS app?")
    wait_answer(page, 2)
    reply = messages(page).nth(1).inner_text()
    assert "cross-platform mobile applications" in reply
    page.get_by_text("Sources (1)").click()
    assert "mobile-apps.html" in messages(page).nth(1).inner_text()
    send_text(page, "and chatbots?")
    wait_answer(page, 4)
    assert "chatbot" in messages(page).nth(3).inner_text().lower()
    page.get_by_text("Sources (1)").last.click()
    assert "ai-services/chatbots.md" in messages(page).nth(3).inner_text()
    convs = backend_conv(stack, page)
    assert len(convs) == 1 and [t["role"] for t in convs[0]["turns"]] == ["user", "assistant"] * 2  # one conversation across turns
    shot(page, "02_text_chat")


def test_unsupported_question_says_so_in_browser(page):
    send_text(page, "What is the weather like in Paris tomorrow?")
    wait_answer(page, 2)
    # check the answer paragraph itself (the status badge also says "verified documentation" and must not satisfy this)
    answer = messages(page).nth(1).locator("p").first.inner_text().lower()
    assert "could not find verified information" in answer and "can't share that" not in answer
    assert "not found in verified documentation" in messages(page).nth(1).inner_text()
    assert page.get_by_role("button", name="Have the team contact me").is_visible()


def test_avatar_voice_turn_records_transcribes_speaks_and_returns_to_idle(page, stack):
    select_mode(page, "Avatar")
    f = voice_frame(page)
    assert "Ready" in f.locator("#status").inner_text()
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Listening").wait_for()
    shot(page, "03_avatar_listening")
    page.wait_for_timeout(1800)  # fake mic emits a beep tone; give the recorder real audio
    f.locator("#mic").click()  # send
    f.locator("#status").filter(has_text=re.compile("Transcribing|Thinking")).wait_for()
    shot(page, "04_avatar_thinking")
    f.locator("#status").filter(has_text="Speaking").wait_for(timeout=40000)
    # mouth should move with the audio loudness while speaking
    opens = []
    for _ in range(14):
        opens.append(float(f.locator("#mouth").get_attribute("ry")))
        page.wait_for_timeout(120)
    shot(page, "05_avatar_speaking")
    assert max(opens) - min(opens) > 1.0, f"mouth did not move: {opens}"
    # chat shows the transcript as a voice message plus the spoken answer's text
    assert "what services do you offer" in messages(page).nth(0).inner_text().lower()
    f.locator("#status").filter(has_text="Ready").wait_for(timeout=15000)  # playback ended -> idle
    mouth_closed = float(f.locator("#mouth").get_attribute("ry"))
    assert mouth_closed < 4
    conv = backend_conv(stack, page)[0]
    assert [t["mode"] for t in conv["turns"]] == ["avatar", "avatar"]
    assert "[1]" not in conv["turns"][1]["text"]  # spoken answers carry no citation markers
    assert page.js_errors == []


def test_interrupting_the_avatar_marks_previous_answer_and_takes_new_input(page, stack):
    select_mode(page, "Avatar")
    f = voice_frame(page)

    def talk():
        f.locator("#mic").click()
        f.locator("#status").filter(has_text="Listening").wait_for()
        page.wait_for_timeout(1500)
        f.locator("#mic").click()

    talk()
    f.locator("#status").filter(has_text="Speaking").wait_for(timeout=40000)
    f.locator("#mic").click()  # "Interrupt & talk" while speaking
    f.locator("#status").filter(has_text="Listening").wait_for()
    shot(page, "06_interrupt_listening")
    page.wait_for_timeout(1500)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Speaking").wait_for(timeout=40000)
    turns = backend_conv(stack, page)[0]["turns"]
    assert len(turns) == 4 and turns[1]["interrupted"] is True and turns[3]["interrupted"] is False


def test_stop_button_stops_speech_without_sending_anything(page, stack):
    select_mode(page, "Voice")
    f = voice_frame(page)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Listening").wait_for()
    page.wait_for_timeout(1500)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Speaking").wait_for(timeout=40000)
    f.locator("#stop").click()
    f.locator("#status").filter(has_text="Ready").wait_for()
    page.wait_for_timeout(800)
    assert len(backend_conv(stack, page)[0]["turns"]) == 2


def test_cancel_recording_sends_nothing(page, stack):
    select_mode(page, "Voice")
    f = voice_frame(page)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Listening").wait_for()
    f.locator("#cancel").click()
    f.locator("#status").filter(has_text="Ready").wait_for()
    page.wait_for_timeout(1000)
    assert backend_conv(stack, page) == []


def test_too_short_recording_is_rejected_client_side(page, stack):
    select_mode(page, "Voice")
    f = voice_frame(page)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Listening").wait_for()
    f.locator("#mic").click()  # immediately stop
    f.locator("#msg").filter(has_text="too short").wait_for()
    assert backend_conv(stack, page) == []


def test_typed_message_in_voice_mode_stops_audio_and_is_answered(page, stack):
    select_mode(page, "Avatar")
    f = voice_frame(page)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Listening").wait_for()
    page.wait_for_timeout(1500)
    f.locator("#mic").click()
    f.locator("#status").filter(has_text="Speaking").wait_for(timeout=40000)
    send_text(page, "Do you build chatbots?")
    f.locator("#status").filter(has_text=re.compile("Ready|Speaking")).first.wait_for()
    wait_answer(page, 4)
    assert len(backend_conv(stack, page)[0]["turns"]) == 4


def test_microphone_permission_denied_shows_guidance(browser, stack):
    ctx = browser.new_context(permissions=[])  # nothing granted
    pg = ctx.new_page()
    pg.set_default_timeout(25000)
    pg.add_init_script("""navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), {name: 'NotAllowedError'}));""")
    pg.goto(stack["ui"])
    pg.get_by_text("Welcome to Fixture Corp").wait_for()
    select_mode(pg, "Voice")
    f = voice_frame(pg)
    f.locator("#mic").click()
    f.locator("#msg").filter(has_text="permission was denied").wait_for()
    assert "type your question instead" in f.locator("#msg").inner_text()
    shot(pg, "07_mic_denied")
    send_text(pg, "Do you build chatbots?")  # text still works
    wait_answer(pg, 2)
    ctx.close()


def test_missing_microphone_and_unsupported_browser_messages(browser, stack):
    for script, expected in [
        ("navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('x'), {name: 'NotFoundError'}));", "No microphone was found"),
        ("delete window.MediaRecorder;", "can't record audio"),
    ]:
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.set_default_timeout(25000)
        pg.add_init_script(script)
        pg.goto(stack["ui"])
        pg.get_by_text("Welcome to Fixture Corp").wait_for()
        select_mode(pg, "Voice")
        f = voice_frame(pg)
        f.locator("#mic").click()
        f.locator("#msg").filter(has_text=expected).wait_for()
        ctx.close()


def test_lead_form_in_browser_saves_with_consent(page, stack):
    page.get_by_role("button", name=re.compile("Talk to the team")).click()
    page.get_by_text("This chat can't contact the team directly").wait_for()
    page.get_by_label("Name *").fill("Jane Doe")
    page.get_by_label("Work email *").fill("jane@acme.io")
    page.get_by_label("What are you looking to build?").fill("HR chatbot")
    page.get_by_role("button", name="Submit").click()
    page.get_by_text("Please tick the consent box").wait_for()
    page.get_by_text("I agree that the details above").click()
    page.get_by_role("button", name="Submit").click()
    page.get_by_text("saved in the team's follow-up queue").wait_for()
    shot(page, "08_lead_saved")
    db = sqlite3.connect(stack["data"] / "leads.sqlite3")
    row = db.execute("SELECT name, email, project_type, status, follow_up_requested FROM leads").fetchone()
    assert row == ("Jane Doe", "jane@acme.io", "HR chatbot", "handoff_requested", 1)


def test_new_conversation_and_refresh_start_clean(page, stack):
    send_text(page, "Do you build chatbots?")
    wait_answer(page, 2)
    page.get_by_role("button", name=re.compile("New conversation")).click()
    page.get_by_text("Welcome to Fixture Corp").wait_for()
    assert messages(page).count() == 1
    send_text(page, "hello again")
    wait_answer(page, 2)
    page.reload()
    page.get_by_text("Welcome to Fixture Corp").wait_for()
    assert messages(page).count() == 1  # refresh starts a fresh view (restore-on-refresh is opt-in)
    send_text(page, "Do you build chatbots?")
    wait_answer(page, 2)


def test_backend_outage_shows_error_and_retry_recovers(page, stack):
    send_text(page, "Do you build chatbots?")
    wait_answer(page, 2)
    backend = stack["procs"]["backend"]
    backend.terminate()
    backend.wait(timeout=10)
    send_text(page, "tell me more")
    page.get_by_text("can't reach the assistant service").first.wait_for()
    page.get_by_role("button", name="Retry").wait_for()
    shot(page, "09_backend_down")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "E2E_DATA_DIR": str(stack["data"]), "E2E_OLLAMA_URL": stack["procs"]["ollama"].args[-1] and
           f"http://127.0.0.1:{stack['procs']['ollama'].args[-1]}", "E2E_BACKEND_PORT": stack["backend"].rsplit(":", 1)[1]}
    stack["procs"]["backend"] = subprocess.Popen([sys.executable, "tests/e2e/server.py"], cwd=ROOT, env=env)
    _wait(stack["backend"] + "/health")
    page.get_by_role("button", name="Retry").click()
    wait_answer(page, 4)  # new backend has no memory of the old conversation: it starts fresh instead of failing
    assert page.get_by_role("button", name="Retry").count() == 0
