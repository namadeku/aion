"""Optional voice-input settings with safe defaults.

The config schema is shared and may not know these fields yet: every value is read with
``getattr(section, name, default)``, so the voice stack works with the current schema and picks
the fields up as soon as they are added (see docs/voice-input.md for the proposed schema).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aion.config import Config


@dataclass(frozen=True)
class VoiceTuning:
    #: Raise the level of quiet utterances before recognition (up to +30 dB).
    normalize: bool = True
    #: Spectral noise gate before recognition. Off: it helps only stationary noise.
    denoise: bool = False
    #: Fix frequent recognition slips ("скрин шот", "сделает погромче") before routing.
    postprocess: bool = True
    #: User dictionary of recognition errors: {"wrong": "right"}.
    corrections: dict[str, str] = field(default_factory=dict)
    #: Accept the name after the command ("который час, Аион") when the router knows the command.
    tail_name: bool = True
    #: Longest utterance (s) that is transcribed whole after a negative start check, to find a
    #: trailing name. 0 = automatic: 8 s on a GPU, 4 s on the CPU (each check is a full decode).
    tail_check_max_s: float = 0.0
    #: The mic is treated as "assistant is speaking" this long after its voice stops.
    echo_holdoff_s: float = 0.6
    #: Start transcribing after this much silence, before the utterance is closed (0 = off).
    speculative_ms: int = 250

    @classmethod
    def from_config(cls, config: Config) -> VoiceTuning:
        audio, stt, wake = config.audio, config.stt, config.wakeword
        base = cls()
        return cls(
            normalize=bool(getattr(audio, "normalize", base.normalize)),
            denoise=bool(getattr(audio, "denoise", base.denoise)),
            postprocess=bool(getattr(stt, "postprocess", base.postprocess)),
            corrections=dict(getattr(stt, "corrections", base.corrections)),
            tail_name=bool(getattr(wake, "tail_name", base.tail_name)),
            tail_check_max_s=float(getattr(wake, "tail_check_max_s", base.tail_check_max_s)),
            echo_holdoff_s=float(getattr(audio, "echo_holdoff_s", base.echo_holdoff_s)),
            speculative_ms=int(getattr(audio, "speculative_ms", base.speculative_ms)),
        )
