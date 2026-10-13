"""Signal conditioning, hallucination filtering and text post-processing for recognition."""

from __future__ import annotations

import numpy as np
import pytest

from aion.audio.dsp import (
    normalize_level,
    prepare_for_stt,
    remove_dc,
    rms_dbfs,
    spectral_gate,
    to_float,
    to_pcm,
)
from aion.stt.base import drop_hallucinations, is_silence
from aion.text.asr import ordinal_dates_to_digits, postprocess

RNG = np.random.default_rng(1)


def tone(seconds: float, amplitude: float, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# -- dsp -------------------------------------------------------------------------------


def test_normalize_level_boosts_quiet_speech_only() -> None:
    quiet = tone(1.0, 0.02)
    boosted = normalize_level(quiet)
    assert 0.45 < float(np.max(np.abs(boosted))) <= 0.51
    loud = tone(1.0, 0.8)
    assert normalize_level(loud) is loud  # never attenuates


def test_normalize_level_ignores_room_noise() -> None:
    noise = (RNG.standard_normal(16000) * 0.0003).astype(np.float32)
    assert float(np.max(np.abs(normalize_level(noise)))) < 0.01


def test_normalize_level_gain_is_capped() -> None:
    faint = tone(1.0, 0.004)  # just above the noise gate: 30 dB at most
    assert float(np.max(np.abs(normalize_level(faint)))) <= 0.004 * 10 ** (30 / 20) * 1.01


def test_remove_dc() -> None:
    x = (tone(0.5, 0.1) + 0.2).astype(np.float32)
    assert abs(float(np.mean(remove_dc(x)))) < 1e-6


def test_spectral_gate_reduces_stationary_noise() -> None:
    speech = tone(1.0, 0.3) * np.hanning(16000).astype(np.float32)
    noise = (RNG.standard_normal(16000 * 2) * 0.03).astype(np.float32)
    clip = np.concatenate([noise[:8000], speech + noise[8000:24000], noise[24000:]])
    gated = spectral_gate(clip)
    assert len(gated) == len(clip)
    assert float(np.std(gated[:6000])) < 0.5 * float(np.std(clip[:6000]))  # noise-only part
    assert float(np.max(np.abs(gated[8000:24000]))) > 0.15  # speech survives


def test_prepare_for_stt_roundtrip_types() -> None:
    pcm = to_pcm((tone(0.5, 0.02) + 0.01).astype(np.float32))
    out = prepare_for_stt(pcm, normalize=True, denoise=False)
    assert out.dtype == np.int16
    assert rms_dbfs(out) > rms_dbfs(pcm)
    assert prepare_for_stt(np.zeros(0, dtype=np.int16)).size == 0
    assert to_float(out).max() <= 1.0


# -- hallucinations ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Продолжение следует...",
        "Субтитры сделал DimaTorzok",
        "Редактор субтитров А.Семкин Корректор А.Егорова",
        "Спасибо за просмотр!",
        "Подписывайтесь на канал",
        "Да. Да. Да. Да. Да. Да.",
        "Ааааааааааааааааа",
        "...",
        "Thanks for watching!",
    ],
)
def test_hallucinations_are_dropped(text: str) -> None:
    assert drop_hallucinations(text) == ""


@pytest.mark.parametrize(
    "text",
    ["включи субтитры", "Открой телеграм.", "да", "Нет, не надо", "который час"],
)
def test_real_commands_survive_hallucination_filter(text: str) -> None:
    assert drop_hallucinations(text) == text


def test_is_silence() -> None:
    assert is_silence(np.zeros(16000, dtype=np.int16))
    assert is_silence(to_pcm((RNG.standard_normal(16000) * 0.0005).astype(np.float32)))
    assert not is_silence(to_pcm(tone(1.0, 0.05)))


# -- text post-processing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("Постав таймер на 10 минут", "поставь таймер на 10 минут"),
        ("сделает погромче", "сделай погромче"),
        ("сделай скрин шот", "сделай скриншот"),
        ("открой You Tube", "открой ютуб"),
        ("поставь таймер на 10минут", "поставь таймер на 10 минут"),
        ("напомни двадцатого марта", "напомни 20 марта"),
        ("двадцать первого мая", "21 мая"),
        ("  который   час  ", "который час"),
        ("", ""),
    ],
)
def test_postprocess(raw: str, fixed: str) -> None:
    assert postprocess(raw).lower() == fixed


def test_postprocess_is_idempotent_and_keeps_normal_text() -> None:
    text = "Открой Telegram и напиши маме"
    assert postprocess(text) == text
    once = postprocess("сделает погромче")
    assert postprocess(once) == once


def test_postprocess_user_dictionary() -> None:
    assert postprocess("включи Аллису", {"аллису": "алису"}) == "включи алису"


def test_ordinals_only_before_a_month() -> None:
    assert ordinal_dates_to_digits("первого человека") == "первого человека"
    assert ordinal_dates_to_digits("первого апреля") == "1 апреля"
