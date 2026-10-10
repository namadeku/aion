"""Microsoft Edge online TTS (many natural voices, needs internet)."""

from __future__ import annotations

import asyncio

import numpy as np

from aion.tts.base import Audio, TtsEngine, VoiceInfo


class EdgeEngine(TtsEngine):
    name = "edge"
    offline = False
    reads_numbers = True

    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        import edge_tts

        percent = round((rate - 1.0) * 100)
        communicate = edge_tts.Communicate(
            text, voice or "ru-RU-DmitryNeural", rate=f"{percent:+d}%"
        )
        mp3 = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])  # pyright: ignore[reportTypedDictNotRequiredAccess]
        return await asyncio.to_thread(_decode_mp3, bytes(mp3))

    async def voices(self, language: str = "ru") -> list[VoiceInfo]:
        import edge_tts

        try:
            listed = await edge_tts.list_voices()
        except Exception:
            listed = []
        voices = [
            VoiceInfo(v["ShortName"], v["FriendlyName"], v["Locale"], v["Gender"].lower())
            for v in listed
            if v["Locale"].lower().startswith(language)
        ]
        if not voices and language == "ru":
            voices = [
                VoiceInfo("ru-RU-DmitryNeural", "Dmitry", "ru-RU", "male"),
                VoiceInfo("ru-RU-SvetlanaNeural", "Svetlana", "ru-RU", "female"),
            ]
        return voices


def _decode_mp3(data: bytes) -> Audio:
    import miniaudio

    if not data:
        return Audio(np.zeros(0, dtype=np.float32), 24000)
    decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1)
    samples = np.frombuffer(decoded.samples, dtype=np.float32).copy()
    return Audio(samples, decoded.sample_rate)
