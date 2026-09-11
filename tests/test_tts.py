import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_sends_balanced_latency_and_assembles_stream():
    import tts
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3" + b"\x00" * 100)

    async with _client(handler) as c:
        r = await tts.synthesize_chunk("Good evening, sir.", api_key="k", voice_id="v", client=c)
    assert seen["url"] == tts.FISH_TTS_URL
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {"text": "Good evening, sir.", "reference_id": "v",
                            "format": "mp3", "mp3_bitrate": 128, "latency": "balanced"}
    assert r is not None and r.audio.startswith(b"ID3") and len(r.audio) == 103
    assert r.first_byte_sec >= 0 and r.total_sec >= r.first_byte_sec


@pytest.mark.asyncio
async def test_non_200_returns_none():
    import tts
    async with _client(lambda req: httpx.Response(401, content=b"nope")) as c:
        assert await tts.synthesize_chunk("x", api_key="k", voice_id="v", client=c) is None


@pytest.mark.asyncio
async def test_transport_error_returns_none():
    import tts

    def boom(request):
        raise httpx.ConnectError("down")

    async with _client(boom) as c:
        assert await tts.synthesize_chunk("x", api_key="k", voice_id="v", client=c) is None


@pytest.mark.asyncio
async def test_empty_text_or_missing_key_short_circuits():
    import tts
    calls = []

    async with _client(lambda req: calls.append(1) or httpx.Response(200, content=b"x")) as c:
        assert await tts.synthesize_chunk("   ", api_key="k", voice_id="v", client=c) is None
        assert await tts.synthesize_chunk("hi", api_key="", voice_id="v", client=c) is None
    assert calls == []


# --- provider selection ------------------------------------------------------

def test_provider_defaults_to_fish_and_rejects_junk(monkeypatch):
    import tts
    monkeypatch.delenv("JARVIS_TTS", raising=False)
    assert tts.provider() == "fish"
    monkeypatch.setenv("JARVIS_TTS", "EDGE")
    assert tts.provider() == "edge"
    monkeypatch.setenv("JARVIS_TTS", "BROWSER")
    assert tts.provider() == "browser"
    monkeypatch.setenv("JARVIS_TTS", "elevenlabs")
    assert tts.provider() == "fish"


@pytest.mark.asyncio
async def test_browser_provider_never_calls_out(monkeypatch):
    """JARVIS_TTS=browser has no server-side voice by design: it must return
    None for every chunk without touching the network (Fish's client) or
    edge_tts at all — a stray call here would mean the "browser" setting was
    quietly still trying to synthesise something."""
    import tts
    monkeypatch.setenv("JARVIS_TTS", "browser")

    def boom(*a, **kw):
        raise AssertionError("browser provider must not synthesise")

    monkeypatch.setattr(tts, "_synthesize_fish", boom)
    monkeypatch.setattr(tts, "_synthesize_edge", boom)
    assert await tts.synthesize_chunk("hello", api_key="k", voice_id="v") is None


@pytest.mark.asyncio
async def test_edge_provider_assembles_audio_chunks(monkeypatch):
    import tts
    monkeypatch.setenv("JARVIS_TTS", "edge")

    class FakeCommunicate:
        def __init__(self, text, voice):
            self.text, self.voice = text, voice

        async def stream(self):
            yield {"type": "WordBoundary", "data": None}
            yield {"type": "audio", "data": b"\xff\xf3\x64\xc4"}
            yield {"type": "audio", "data": b"rest"}

    monkeypatch.setitem(sys.modules, "edge_tts", type("m", (), {"Communicate": FakeCommunicate}))

    r = await tts.synthesize_chunk("Good evening.", api_key="", voice_id="ignored")
    assert r is not None and r.audio == b"\xff\xf3\x64\xc4rest"


@pytest.mark.asyncio
async def test_edge_provider_missing_package_returns_none(monkeypatch):
    import tts
    monkeypatch.setenv("JARVIS_TTS", "edge")
    monkeypatch.setitem(sys.modules, "edge_tts", None)   # import edge_tts -> ImportError
    assert await tts.synthesize_chunk("hi", api_key="", voice_id="v") is None
