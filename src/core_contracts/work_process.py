"""Framed private pipes for the work processes.

The bootstrap is sent over stdin because it contains the existing DB credentials.
Neither bootstrap nor request bodies appear in process arguments or logs.
"""

import asyncio
import ctypes
import os
import signal
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .model import canonical

FRAME_LIMIT = 128 * 1024
_HEADER = struct.Struct("!I")


def child_environment() -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in ("LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TMP", "TEMP")
        if name in os.environ
    }


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > FRAME_LIMIT:
        raise ValueError("work_frame_too_large")
    return _HEADER.pack(len(payload)) + payload


def read_frame(stream: BinaryIO) -> bytes:
    encoded = stream.read(_HEADER.size)
    if len(encoded) != _HEADER.size:
        raise EOFError
    size = _HEADER.unpack(encoded)[0]
    if size > FRAME_LIMIT:
        raise ValueError("work_frame_too_large")
    payload = stream.read(size)
    if len(payload) != size:
        raise EOFError
    return payload


def write_frame(stream: BinaryIO, payload: bytes) -> None:
    stream.write(encode_frame(payload))
    stream.flush()


def exit_with_parent(parent: int) -> None:
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0):  # Linux PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "cannot bind worker lifetime")
        if os.getppid() != parent:
            raise SystemExit(1)


@dataclass
class WorkProcess:
    process: asyncio.subprocess.Process

    @classmethod
    async def start(
        cls, module: str, configuration: dict[str, Any], *args: str, cwd: Path | None = None
    ) -> WorkProcess:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module,
            str(os.getpid()),
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=os.name == "posix",
        )
        child = cls(process)
        try:
            await child.send(canonical(configuration).encode())
        except BaseException:
            await child.stop()
            raise
        return child

    async def send(self, payload: bytes) -> None:
        writer = self.process.stdin
        assert writer is not None
        writer.write(encode_frame(payload))
        await writer.drain()

    async def receive(self) -> bytes:
        reader = self.process.stdout
        assert reader is not None
        size = _HEADER.unpack(await reader.readexactly(_HEADER.size))[0]
        if size > FRAME_LIMIT:
            raise ValueError("work_frame_too_large")
        return await reader.readexactly(size)

    async def stop(self) -> None:
        if self.process.returncode is None:
            if sys.platform != "win32":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
        if self.process.stdin is not None:
            self.process.stdin.close()
        # drain pipes even after the child filled its outbound buffer
        await self.process.communicate()
