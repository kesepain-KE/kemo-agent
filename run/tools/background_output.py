"""Bounded, capture-ordered five-line snapshot, independent of durable log limits."""
from __future__ import annotations

from collections import deque
import codecs
import locale
import threading

from run.infra import diagnostic_text


class OutputTail:
    def __init__(self):
        self.lock = threading.Lock()
        self.sequence = 0
        self.lines: deque[tuple[int, str, str]] = deque(maxlen=5)
        self.partial: dict[str, tuple[int, str]] = {}
        self.finished: set[str] = set()
        self.decoders: dict[str, codecs.IncrementalDecoder] = {stream: codecs.getincrementaldecoder("utf-8")(errors="strict")
                         for stream in ("stdout", "stderr")}

    def feed(self, stream: str, data: bytes) -> None:
        with self.lock:
            if not data:
                self.finished.add(stream)
            decoder = self.decoders[stream]
            buffered = decoder.getstate()[0]
            try:
                text = decoder.decode(data, final=not data)
            except UnicodeDecodeError:
                encoding = locale.getpreferredencoding(False) or "utf-8"
                decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
                self.decoders[stream] = decoder
                text = decoder.decode(buffered + data, final=not data)
            text = self.partial.pop(stream, (0, ""))[1] + text
            for line in text.splitlines(keepends=True):
                self.sequence += 1
                bounded = diagnostic_text(line.rstrip("\r\n"))[-400:]
                if line.endswith(("\n", "\r")) or not data:
                    self.lines.append((self.sequence, stream, bounded))
                else:
                    self.partial[stream] = (self.sequence, bounded)

    def snapshot(self) -> list[str]:
        with self.lock:
            entries = list(self.lines) + [(seq, name, text)
                                         for name, (seq, text) in self.partial.items()]
            return [f"[{name}] {text}" for _, name, text in sorted(entries)[-5:]]

    def complete(self) -> bool:
        with self.lock:
            return self.finished == {"stdout", "stderr"}
