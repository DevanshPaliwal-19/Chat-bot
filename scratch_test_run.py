import sys
import asyncio
from pathlib import Path
from app.config import Settings
from tests.conftest import FIXTURE_KB, FakeLLM, FakeSTT, FakeTTS, build_container
from app.rag.embeddings import HashingEmbedder
from app.schemas import ChatRequest

async def test_step():
    print("In test_step", flush=True)
    tmp_path = Path("data/test_tmp")
    s = Settings(
        _env_file=None, company_name="Fixture Corp", assistant_name="Fixie", company_website="https://fixture.example/",
        kb_dir=FIXTURE_KB, data_dir=tmp_path / "data", embedding_provider="hashing", log_level="WARNING",
        llm_concurrency=2, rate_limit_requests=1000, analysis_enabled=False,
    )
    fake_llm = FakeLLM()
    c = build_container(s, llm=fake_llm, embedder=HashingEmbedder(), stt=FakeSTT(), tts=FakeTTS())
    print("Container built", flush=True)
    req = ChatRequest(session_id="sess_test", message="Can you build an iOS app for us?")
    print("Req created", flush=True)
    resp = await c.engine.handle(req)
    print("Processed resp:", resp.response_status, flush=True)
    print("Answer:", resp.answer[:60], flush=True)
    print("is_truncated:", resp.is_truncated, flush=True)
    print("finish_reason:", resp.finish_reason, flush=True)

asyncio.run(test_step())
