"""
tts.py — speech synthesis, one request per sentence chunk.

Three providers, chosen by `JARVIS_TTS`:

  fish    (default) — Fish Audio, the JARVIS MCU voice. Paid, needs FISH_API_KEY.
  edge              — Microsoft Edge's online neural voices via the `edge-tts`
                      package. Free, no key, no account; a network call to
                      Microsoft per chunk. Voice set by JARVIS_EDGE_VOICE
                      (default en-GB-RyanNeural).
  browser           — no server-side synthesis at all: every chunk is sent to
                      the browser as a plain `text` frame and spoken with the
                      OS's own Web Speech API (server.py's BROWSER_TTS is
                      forced on for this provider). The simplest, most basic
                      voice available, no network call, and — because it skips
                      the base64-MP3-over-WebSocket path entirely — useful for
                      telling whether a symptom lives in that pipeline or not.

`fish` and `edge` return one complete MP3 per chunk (the browser decodes one
per chunk) and report time-to-first-byte for the latency log. `browser`
produces no audio bytes here at all — see `frontend/src/voice.ts`
`createBrowserSpeech` for where it actually speaks.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("jarvis.tts")

FISH_TTS_URL = "https://api.fish.audio/v1/tts"

# en-GB-RyanNeural (m) and en-GB-SoniaNeural (f) are the closest free stand-ins
# for the butler. `edge-tts --list-voices` has the rest.
EDGE_VOICE = os.getenv("JARVIS_EDGE_VOICE", "en-GB-RyanNeural")


def provider() -> str:
    """`fish`, `edge` or `browser`. Read live so a test (or a settings write)
    can change it without reimporting the module."""
    p = os.getenv("JARVIS_TTS", "fish").strip().lower()
    return p if p in ("fish", "edge", "browser") else "fish"


@dataclass
class SynthResult:
    audio: bytes
    first_byte_sec: float
    total_sec: float


async def synthesize_chunk(text: str, *, api_key: str, voice_id: str,
                           client: Optional[httpx.AsyncClient] = None,
                           latency: str = "balanced", timeout: float = 15.0) -> Optional[SynthResult]:
    text = (text or "").strip()
    if not text:
        return None
    p = provider()
    if p == "browser":
        # No server-side voice at all, by design: the scheduler's null-audio
        # path already turns this into a `text` frame for the browser to
        # speak. Not a failure, so nothing is logged.
        return None
    if p == "edge":
        return await _synthesize_edge(text, timeout=timeout)
    return await _synthesize_fish(text, api_key=api_key, voice_id=voice_id,
                                  client=client, latency=latency, timeout=timeout)


async def _synthesize_fish(text: str, *, api_key: str, voice_id: str,
                           client: Optional[httpx.AsyncClient], latency: str,
                           timeout: float) -> Optional[SynthResult]:
    if not api_key:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    t0 = time.monotonic()
    first: Optional[float] = None
    buf = bytearray()
    try:
        async with client.stream(
            "POST", FISH_TTS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"text": text, "reference_id": voice_id, "format": "mp3", "mp3_bitrate": 128, "latency": latency},
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                log.error(f"TTS {resp.status_code} for {text[:40]!r}")
                return None
            async for part in resp.aiter_bytes():
                if first is None:
                    first = time.monotonic() - t0
                buf.extend(part)
    except (httpx.HTTPError, OSError) as e:
        log.error(f"TTS error: {e}")
        return None
    finally:
        if own:
            try:
                await client.aclose()
            except Exception as e:      # never turn a clean None into an exception
                log.debug(f"TTS client close failed: {e}")
    if not buf:
        return None
    return SynthResult(bytes(buf), first if first is not None else 0.0, time.monotonic() - t0)


async def _synthesize_edge(text: str, *, timeout: float) -> Optional[SynthResult]:
    """Microsoft Edge neural TTS. `edge-tts` maintains the (undocumented,
    token-gated) protocol; we only assemble its audio chunks."""
    try:
        import edge_tts
    except ImportError:
        log.error("JARVIS_TTS=edge but the `edge-tts` package is not installed "
                  "(pip install edge-tts)")
        return None
    t0 = time.monotonic()
    first: Optional[float] = None
    buf = bytearray()
    try:
        communicate = edge_tts.Communicate(text, EDGE_VOICE)
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                if first is None:
                    first = time.monotonic() - t0
                buf.extend(chunk["data"])
    except Exception as e:      # edge-tts raises its own exception types
        log.error(f"edge TTS error: {e}")
        return None
    if not buf:
        return None
    return SynthResult(bytes(buf), first if first is not None else 0.0, time.monotonic() - t0)
