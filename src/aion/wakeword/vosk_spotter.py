"""Wake word via streaming Vosk + fuzzy name matching.

No training needed: the assistant can be renamed at runtime. Partial recognition results are
checked after every frame, so the name is caught while the user is still speaking.
"""

from __future__ import annotations

import json
from typing import Any

from aion.audio.vad import Frame
from aion.wakeword.base import WakeWordDetector, find_name, sensitivity_to_threshold


class VoskSpotter(WakeWordDetector):
    def __init__(self, model: Any, names: list[str], sensitivity: float = 0.5) -> None:
        import vosk

        self._model = model
        self._recognizer = vosk.KaldiRecognizer(model, 16000)
        self.names = names
        self.threshold = sensitivity_to_threshold(sensitivity)
        self._last_partial = ""

    def set_names(self, names: list[str]) -> None:
        self.names = names

    def reset(self) -> None:
        self._recognizer.Reset()
        self._last_partial = ""

    def process(self, frame: Frame) -> str | None:
        if self._recognizer.AcceptWaveform(frame.tobytes()):
            text = str(json.loads(self._recognizer.Result()).get("text", ""))
            self._last_partial = ""
        else:
            text = str(json.loads(self._recognizer.PartialResult()).get("partial", ""))
            if text == self._last_partial:
                return None
            self._last_partial = text
        if not text:
            return None
        found = find_name(text, self.names, self.threshold)
        if found is None:
            return None
        self.reset()
        return found[0]
