"""Minimal RFC 6455 server-side WebSocket.

Written by hand rather than pulled in as a dependency, for one reason that
matters at this layer: GARIS starts with Windows and runs all day. The process
that does that should carry the smallest possible dependency surface, and the
subset a local UI needs — text frames, ping/pong, close — is about 150 lines.

Only what a server needs: no client mode, no extensions, no fragmentation of
outgoing frames (payloads here are small JSON events).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from dataclasses import dataclass

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_PAYLOAD = 1 << 20  # 1 MiB: a local UI never legitimately sends more


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(client_key: str) -> bytes:
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(client_key)}\r\n"
        "\r\n"
    ).encode("ascii")


def encode_frame(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    """Server frames are never masked (RFC 6455 §5.1)."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 1 << 16:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


def encode_text(text: str) -> bytes:
    return encode_frame(text.encode("utf-8"), OP_TEXT)


def encode_close(code: int = 1000, reason: str = "") -> bytes:
    return encode_frame(struct.pack("!H", code) + reason.encode("utf-8"), OP_CLOSE)


@dataclass(slots=True)
class Frame:
    opcode: int
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    @property
    def is_close(self) -> bool:
        return self.opcode == OP_CLOSE


class ProtocolError(Exception):
    """The peer sent something that is not a valid client frame."""


async def read_frame(
    reader: asyncio.StreamReader, *, expect_mask: bool = True
) -> Frame | None:
    """Read one frame, reassembling continuations. None on clean disconnect.

    ``expect_mask`` follows RFC 6455 §5.3: client frames must be masked, server
    frames must not be. The server always reads with it on; a client (the test
    suite, a diagnostic script) reads with it off.
    """
    opcode: int | None = None
    chunks: list[bytes] = []

    while True:
        header = await reader.readexactly(2) if not reader.at_eof() else b""
        if len(header) < 2:
            return None

        final = bool(header[0] & 0x80)
        this_opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]

        if length > MAX_PAYLOAD:
            raise ProtocolError(f"Ramka {length} B przekracza limit {MAX_PAYLOAD} B")
        if expect_mask and not masked:
            # A client that does not mask is either broken or hostile; both are
            # reasons to hang up rather than guess.
            raise ProtocolError("Klient musi maskować ramki")
        if not expect_mask and masked:
            raise ProtocolError("Serwer nie powinien maskować ramek")

        if masked:
            mask = await reader.readexactly(4)
            raw = await reader.readexactly(length) if length else b""
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(raw))
        else:
            payload = await reader.readexactly(length) if length else b""

        if this_opcode != OP_CONTINUATION:
            opcode = this_opcode
        chunks.append(payload)

        if final:
            return Frame(opcode if opcode is not None else OP_TEXT, b"".join(chunks))


class Connection:
    """One live WebSocket. Writes are serialised so concurrent pushes cannot interleave."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()
        self.closed = False

    async def send_text(self, text: str) -> None:
        await self._send(encode_text(text))

    async def send_pong(self, payload: bytes) -> None:
        await self._send(encode_frame(payload, OP_PONG))

    async def _send(self, data: bytes) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                self.writer.write(data)
                await self.writer.drain()
            except (ConnectionError, RuntimeError):
                self.closed = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.write(encode_close(code, reason))
            await self.writer.drain()
        except (ConnectionError, RuntimeError):
            pass
        finally:
            self.writer.close()

    @property
    def peer(self) -> str:
        info = self.writer.get_extra_info("peername")
        return f"{info[0]}:{info[1]}" if info else "?"


__all__ = [
    "MAX_PAYLOAD",
    "OP_CLOSE",
    "OP_PING",
    "OP_PONG",
    "OP_TEXT",
    "Connection",
    "Frame",
    "ProtocolError",
    "accept_key",
    "encode_close",
    "encode_frame",
    "encode_text",
    "handshake_response",
    "read_frame",
]
