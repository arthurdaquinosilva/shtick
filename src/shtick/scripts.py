"""An opened script split into top-level commands, with a position for stepping through it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from shtick.syntax import Chunk, split_script


@dataclass
class Script:
    path: Path
    chunks: list[Chunk]
    args: list[str] = field(default_factory=list)
    pos: int = 0  # index of the next chunk to run
    breakpoints: set[int] = field(default_factory=set)  # line numbers; %run stops before their command

    @classmethod
    def load(cls, path: str | os.PathLike, args: list[str] | None = None) -> Script:
        p = Path(path).expanduser().resolve()
        return cls(p, split_script(p.read_text()), list(args or []))

    def reload(self) -> None:
        """Re-read the file, keeping the position at the same line."""
        line = self.chunks[self.pos].start if self.pos < len(self.chunks) else None
        self.chunks = split_script(self.path.read_text())
        if line is None:
            self.pos = len(self.chunks)
        else:
            self.pos = next((i for i, c in enumerate(self.chunks) if c.end >= line), len(self.chunks))

    def chunk_at(self, line: int) -> int | None:
        """Index of the command that contains `line` (or the first one after it)."""
        return next((i for i, c in enumerate(self.chunks) if c.end >= line), None)

    def breakpoint_chunks(self) -> set[int]:
        return {i for line in self.breakpoints if (i := self.chunk_at(line)) is not None}

    @property
    def done(self) -> bool:
        return self.pos >= len(self.chunks)

    @property
    def name(self) -> str:
        return self.path.name

    def label(self, index: int) -> str:
        return f"{self.name}:{self.chunks[index].lines}  ({index + 1}/{len(self.chunks)})"
