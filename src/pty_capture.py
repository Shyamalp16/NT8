"""Capture an exact JSON payload from a Windows PTY without terminal echo."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


STD_INPUT_HANDLE = -10
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004


def capture(output: Path, expected_chars: int) -> None:
    if os.name != "nt":
        raise RuntimeError("PTY capture is only supported on Windows")
    if expected_chars < 1:
        raise ValueError("expected_chars must be positive")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    original_mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(original_mode)):
        raise ctypes.WinError(ctypes.get_last_error())

    raw_mode = original_mode.value & ~(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT)
    if not kernel32.SetConsoleMode(handle, raw_mode):
        raise ctypes.WinError(ctypes.get_last_error())

    chunks: list[str] = []
    remaining = expected_chars
    try:
        while remaining:
            capacity = min(65_536, remaining)
            buffer = ctypes.create_unicode_buffer(capacity)
            chars_read = ctypes.c_uint32()
            if not kernel32.ReadConsoleW(
                handle,
                buffer,
                capacity,
                ctypes.byref(chars_read),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if chars_read.value == 0:
                raise EOFError(f"PTY closed with {remaining} characters remaining")
            chunks.append(buffer[: chars_read.value])
            remaining -= chars_read.value
    finally:
        kernel32.SetConsoleMode(handle, original_mode.value)

    payload = "".join(chunks).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as handle_file:
        handle_file.write(payload)
        temporary = Path(handle_file.name)
    os.replace(temporary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-chars", type=int, required=True)
    args = parser.parse_args()
    capture(args.output, args.expected_chars)


if __name__ == "__main__":
    main()
