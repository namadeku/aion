"""Voice effects: J.A.R.V.I.S.-style "metallic" voice, radio, plus volume."""

from __future__ import annotations

import numpy as np

from aion.audio.player import Samples


def _comb(x: Samples, delay: int, gain: float) -> Samples:
    y = x.copy()
    if delay < len(x):
        y[delay:] += gain * x[:-delay]
    return y


def _reverb(x: Samples, sr: int, seconds: float = 0.35, wet: float = 0.18) -> Samples:
    n = int(sr * seconds)
    rng = np.random.default_rng(7)  # deterministic impulse response
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-np.linspace(0, 6, n)).astype(
        np.float32
    )
    ir /= np.sqrt(np.sum(ir * ir))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    tail = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x) + n]
    out = np.zeros(len(x) + n, dtype=np.float32)
    out[: len(x)] = x
    return (out * (1 - wet) + wet * tail.astype(np.float32)).astype(np.float32)


def metallic(x: Samples, sr: int) -> Samples:
    """Subtle ring modulation + short combs (helmet resonance) + light reverb."""
    t = np.arange(len(x), dtype=np.float32) / sr
    ring = x * (0.75 + 0.25 * np.sin(2 * np.pi * 45.0 * t)).astype(np.float32)
    y = _comb(ring, int(sr * 0.0045), 0.35)
    y = _comb(y, int(sr * 0.0071), 0.25)
    return _reverb(y.astype(np.float32), sr)


def radio(x: Samples, sr: int) -> Samples:
    """Band-pass 300-3400 Hz + soft clipping."""
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    spectrum[(freqs < 300) | (freqs > 3400)] = 0
    y = np.fft.irfft(spectrum, len(x)).astype(np.float32)
    return np.tanh(2.5 * y).astype(np.float32) * 0.6


def apply(x: Samples, sr: int, effect: str, volume: float = 1.0) -> Samples:
    if effect == "metallic":
        x = metallic(x, sr)
    elif effect == "radio":
        x = radio(x, sr)
    if volume != 1.0:
        x = x * volume
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.99:
        x = x * (0.99 / peak)
    return x.astype(np.float32, copy=False)
