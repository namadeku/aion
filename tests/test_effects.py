"""Voice effects: the ``digital`` hologram colour."""

from __future__ import annotations

import time

import numpy as np
import pytest

from aion.audio import effects
from aion.config.schema import VoiceConfig

SR = 48000


def speechlike(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float32) / SR
    sig = 0.5 * np.sin(2 * np.pi * 180 * t) + 0.2 * np.sin(2 * np.pi * 1200 * t)
    return (sig * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)


def test_digital_shape_dtype_finite() -> None:
    x = speechlike()
    y = effects.digital(x, SR)
    assert y.dtype == np.float32
    assert y.ndim == 1
    assert len(y) >= len(x)
    assert np.all(np.isfinite(y))
    assert not np.allclose(y[: len(x)], x, atol=1e-3)


def test_digital_deterministic() -> None:
    x = speechlike()
    assert np.array_equal(effects.digital(x, SR), effects.digital(x, SR))


def test_apply_digital_does_not_clip() -> None:
    x = (speechlike() * 2.0).clip(-1, 1)
    y = effects.apply(x, SR, "digital", 1.5)
    assert float(np.max(np.abs(y))) <= 0.99 + 1e-6
    assert y.dtype == np.float32


def test_digital_silence_stays_silent() -> None:
    y = effects.digital(np.zeros(SR, dtype=np.float32), SR)
    assert float(np.max(np.abs(y))) < 1e-4


@pytest.mark.parametrize("n", [0, 1, 2, 10, 100, 500])
def test_digital_short_inputs(n: int) -> None:
    y = effects.digital(np.full(n, 0.3, dtype=np.float32), SR)
    assert y.dtype == np.float32
    assert np.all(np.isfinite(y))


def test_digital_is_fast() -> None:
    x = np.tile(speechlike(1.0), 10)
    effects.digital(x, SR)
    start = time.perf_counter()
    effects.digital(x, SR)
    assert time.perf_counter() - start < 0.5  # generous bound; typically well under 200 ms


def test_config_accepts_digital() -> None:
    assert VoiceConfig(effect="digital").effect == "digital"
