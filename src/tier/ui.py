"""Small terminal UI: ANSI/box-drawing by default, optional rich enhancement."""
from __future__ import annotations

import os
import sys
from typing import Iterable, Optional, Sequence

_ANSI = {
    "bold": "1",
    "cyan": "36",
    "green": "32",
    "yellow": "33",
    "red": "31",
    "dim": "2",
}


class UI:
    """Renders installer and doctor output without requiring third-party packages."""

    def __init__(self, stream=None, use_rich: Optional[bool] = None,
                 color: Optional[bool] = None, env=None):
        self.stream = stream or sys.stdout
        self.env = env if env is not None else os.environ
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        no_color = "NO_COLOR" in self.env or "TIER_NO_COLOR" in self.env
        self.color = (is_tty and not no_color) if color is None else bool(color)
        if use_rich is None:
            use_rich = is_tty and not no_color and "TIER_NO_RICH" not in self.env
        self.console = None
        self._rich = False
        if use_rich:
            try:
                from rich.console import Console

                self.console = Console(stream=self.stream, no_color=not self.color, highlight=False)
                self._rich = True
            except Exception:
                self.console = None

    def _c(self, style: str, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{_ANSI[style]}m{text}\x1b[0m"

    def _write(self, text: str = "") -> None:
        self.stream.write(text + "\n")
        try:
            self.stream.flush()
        except Exception:
            pass

    def header(self, title: str, subtitle: str = "") -> None:
        if self._rich:
            from rich.panel import Panel

            body = title if not subtitle else f"{title}\n[dim]{subtitle}[/dim]"
            self.console.print(Panel.fit(body, border_style="cyan"))
            return
        inner = max(len(title), len(subtitle), 46)
        top = "╭" + "─" * (inner + 2) + "╮"
        bottom = "╰" + "─" * (inner + 2) + "╯"
        self._write(self._c("cyan", top))
        self._write("│ " + self._c("bold", title.ljust(inner)) + " │")
        if subtitle:
            self._write("│ " + self._c("dim", subtitle.ljust(inner)) + " │")
        self._write(self._c("cyan", bottom))

    def info(self, text: str) -> None:
        self._write(self._c("cyan", "• ") + text)

    def success(self, text: str) -> None:
        self._write(self._c("green", "✓ ") + text)

    def warn(self, text: str) -> None:
        self._write(self._c("yellow", "! ") + text)

    def error(self, text: str) -> None:
        self._write(self._c("red", "✗ ") + text)

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
        rows = [[str(c) for c in row] for row in rows]
        if self._rich:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
            for h in headers:
                table.add_column(str(h))
            for row in rows:
                table.add_row(*row)
            self.console.print(table)
            return
        widths = [len(str(h)) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i >= len(widths):
                    widths.append(len(cell))
                else:
                    widths[i] = max(widths[i], len(cell))
        self._write("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
        self._write("  ".join("─" * widths[i] for i in range(len(headers))))
        for row in rows:
            self._write("  ".join(
                (row[i] if i < len(row) else "").ljust(widths[i]) for i in range(len(headers))
            ))

    def raw(self, text: str) -> None:
        self._write(text)

    def panel(self, title: str, lines: Sequence[str]) -> None:
        if self._rich:
            from rich.panel import Panel

            self.console.print(Panel("\n".join(lines) or " ", title=title, border_style="green"))
            return
        content = [str(x) for x in lines] or [""]
        inner = max(len(title), *(len(x) for x in content), 40)
        self._write(self._c("green", "╭─ " + title + " " + "─" * max(0, inner - len(title) - 1) + "╮"))
        for line in content:
            self._write("│ " + line.ljust(inner) + " │")
        self._write(self._c("green", "╰" + "─" * (inner + 2) + "╯"))


def default_ui(plain: bool = False, no_color: bool = False) -> UI:
    if plain:
        return UI(use_rich=False, color=False)
    if no_color:
        return UI(use_rich=None, color=False)
    return UI()
