from __future__ import annotations

from pathlib import Path

from src.control.kill_switch import KillSwitch


def test_kill_switch_file_trip(tmp_path: Path) -> None:
    flag = tmp_path / "halt.flag"
    ks = KillSwitch(str(flag))

    assert ks.triggered is False
    flag.write_text("1", encoding="utf-8")
    assert ks.triggered is True
