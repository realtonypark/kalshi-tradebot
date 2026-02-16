from __future__ import annotations

import signal
from pathlib import Path


class KillSwitch:
    def __init__(self, file_path: str) -> None:
        self.file_path = Path(file_path)
        self._triggered = False

    @property
    def triggered(self) -> bool:
        return self._triggered or self.file_path.exists()

    def trip(self) -> None:
        self._triggered = True

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, _frame: object) -> None:
        _ = signum
        self.trip()
