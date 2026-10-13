"""Cheap signal conditioning for recognition: DC removal, level normalization, noise gating.

Everything works on whole utterances (16 kHz mono int16) right before they go to the STT:
streaming code (VAD, wake spotting) stays untouched, so these steps can never delay or
distort end-pointing. Pure numpy: no scipy/torch.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from aion.audio.vad import SAMPLE_RATE

Pcm = npt.NDArray[np.int16]
Floats = npt.NDArray[np.float32]

_FULL_SCALE = 32768.0


def to_float(audio: Pcm) -> Floats:
    return audio.astype(np.float32) / _FULL_SCALE


def to_pcm(x: Floats) -> Pcm:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def rms_dbfs(audio: Pcm) -> float:
    """Loudness of the whole clip in dBFS (-120 for empty/digital silence)."""
    if audio.size == 0:
        return -120.0
    x = to_float(audio)
    return 20.0 * float(np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-6)))


def remove_dc(x: Floats) -> Floats:
    return (x - float(np.mean(x))).astype(np.float32, copy=False)


def normalize_level(
    x: Floats, *, target_peak: float = 0.5, max_gain_db: float = 30.0, min_peak: float = 0.003
) -> Floats:
    """Bring quiet speech up to ``target_peak`` (never attenuates, never over-amplifies noise).

    The peak is the 99.5th percentile of |x| so a single click does not defeat the gain. Clips
    whose peak is below ``min_peak`` (-50 dBFS) are left alone: that is room noise, and boosting
    it only invites hallucinations.
    """
    if x.size == 0:
        return x
    peak = float(np.percentile(np.abs(x), 99.5))
    if peak < min_peak:
        return x
    gain = min(target_peak / peak, 10 ** (max_gain_db / 20))
    if gain <= 1.0:
        return x
    return np.clip(x * gain, -1.0, 1.0).astype(np.float32, copy=False)


def spectral_gate(
    x: Floats,
    *,
    n_fft: int = 512,
    hop: int = 128,
    strength: float = 1.2,
    floor: float = 0.15,
) -> Floats:
    """Stationary noise reduction by spectral subtraction (Wiener-style gain).

    The noise spectrum is the 20th percentile of every bin's magnitude over time (minimum
    statistics), so no separate "silence" sample is needed. Gains are smoothed across time to
    avoid musical noise. Light-handed on purpose: Whisper dislikes heavy artifacts.
    """
    if x.size < n_fft * 2:
        return x
    window = np.hanning(n_fft).astype(np.float32)
    pad = n_fft
    padded = np.pad(x, (pad, pad + n_fft))
    n_frames = 1 + (len(padded) - n_fft) // hop
    index = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = padded[index] * window
    spectrum = np.fft.rfft(frames, axis=1)
    power = (spectrum.real**2 + spectrum.imag**2).astype(np.float32)
    # power of noise is ~exponentially distributed: its 20th percentile is 0.223 of the mean
    noise = (np.percentile(power, 20, axis=0) / 0.223).astype(np.float32)
    gain = np.sqrt(np.clip(1.0 - strength * noise[None, :] / np.maximum(power, 1e-12), 0.0, 1.0))
    gain = np.maximum(gain, floor)
    kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    smoothed = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="same"), 0, gain)
    frames_out = np.fft.irfft(spectrum * smoothed, n=n_fft, axis=1).astype(np.float32)
    out = np.zeros(len(padded), dtype=np.float32)
    norm = np.zeros(len(padded), dtype=np.float32)
    for i in range(n_frames):  # overlap-add (n_frames is a few hundred at most)
        start = i * hop
        out[start : start + n_fft] += frames_out[i] * window
        norm[start : start + n_fft] += window * window
    result = out / np.maximum(norm, 1e-6)
    return result[pad : pad + len(x)].astype(np.float32, copy=False)


def prepare_for_stt(audio: Pcm, *, normalize: bool = True, denoise: bool = False) -> Pcm:
    """Condition an utterance for recognition (see the module docstring)."""
    if audio.size == 0:
        return audio
    x = remove_dc(to_float(audio))
    if denoise:
        x = spectral_gate(x)
    if normalize:
        x = normalize_level(x)
    return to_pcm(x)


def duration_s(audio: Pcm) -> float:
    return len(audio) / SAMPLE_RATE
