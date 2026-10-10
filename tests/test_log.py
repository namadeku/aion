from __future__ import annotations

from pathlib import Path

import pytest

from aion.log import report_fatal


def test_report_fatal_writes_crash_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AION_HOME", str(tmp_path))
    path = report_fatal("ValueError: плохой конфиг", "Traceback ...")
    assert path == tmp_path / "logs" / "crash.log"
    text = (tmp_path / "logs" / "crash.log").read_text(encoding="utf-8")
    assert "ValueError: плохой конфиг" in text
    assert "Traceback ..." in text
