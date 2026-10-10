"""Audio device discovery and selection (by index or name substring)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    hostapi: str
    inputs: int
    outputs: int
    default_rate: float
    is_default_input: bool
    is_default_output: bool


def list_devices() -> list[Device]:
    import sounddevice as sd

    hostapis: list[dict[str, Any]] = list(sd.query_hostapis())  # pyright: ignore[reportArgumentType]
    default_in, default_out = sd.default.device
    devices: list[Device] = []
    for i, d in enumerate(sd.query_devices()):  # pyright: ignore[reportArgumentType]
        info: dict[str, Any] = dict(d)
        devices.append(
            Device(
                index=i,
                name=str(info["name"]),
                hostapi=str(hostapis[int(info["hostapi"])]["name"]),
                inputs=int(info["max_input_channels"]),
                outputs=int(info["max_output_channels"]),
                default_rate=float(info["default_samplerate"]),
                is_default_input=i == default_in,
                is_default_output=i == default_out,
            )
        )
    return devices


def resolve_device(spec: str | int | None, *, output: bool) -> int | None:
    """None -> system default; int -> index; str -> first device whose name contains it.

    Prefers the WASAPI/MME variant on Windows where the same device is listed several times.
    """
    if spec is None or spec == "":
        return None
    if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
        return int(spec)
    needle = spec.lower()
    candidates = [
        d
        for d in list_devices()
        if needle in d.name.lower() and (d.outputs if output else d.inputs) > 0
    ]
    if not candidates:
        raise ValueError(f"Аудиоустройство «{spec}» не найдено")
    order = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2}
    candidates.sort(key=lambda d: order.get(d.hostapi, 3))
    return candidates[0].index
