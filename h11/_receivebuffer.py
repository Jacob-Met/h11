import re
import sys
from typing import List, Optional, Union

__all__ = ["ReceiveBuffer"]


# Keep allocated writable capacity separate from the logical bytes available
# to the parser. This avoids the bytearray resizing restriction while a
# transport holds a buffer view and allows repeated recv_into() calls without
# intermediate bytes objects. Consumed prefixes are reclaimed on growth.
#
# Track delimiter search positions in physical storage coordinates to avoid
# rescanning large, incomplete header blocks. Our extraction methods only
# return owned bytearrays, never a view into the mutable backing store.
blank_line_regex = re.compile(b"\n\r?\n", re.MULTILINE)


class ReceiveBuffer:
    def __init__(self) -> None:
        self._data = bytearray()
        self._start = 0
        self._end = 0
        self._next_line_search = 0
        self._multiple_lines_search = 0
        self._pending_buffer: Optional[memoryview] = None

    def _reserve(self, count: int) -> None:
        """Ensure space for new bytes without resizing an exported buffer."""
        if len(self._data) - self._end >= count:
            return

        unread = self._end - self._start
        capacity = max(unread + count, len(self._data) * 2, 64)
        data = bytearray(capacity)
        data[:unread] = self._data[self._start : self._end]
        self._next_line_search = max(0, self._next_line_search - self._start)
        self._multiple_lines_search = max(0, self._multiple_lines_search - self._start)
        self._data = data
        self._start = 0
        self._end = unread

    def get_buffer(self, sizehint: int = -1) -> memoryview:
        """Reserve a writable input buffer for socket.recv_into()."""
        if self._pending_buffer is not None:
            raise RuntimeError("previous writable receive buffer not committed")

        if sizehint < -1:
            raise ValueError("sizehint must be >= -1")

        self._reserve(max(16384, sizehint))
        self._pending_buffer = memoryview(self._data)[self._end :]
        return self._pending_buffer

    def buffer_updated(self, nbytes: int) -> None:
        """Commit bytes received into the reserved writable buffer."""
        pending = self._pending_buffer
        if pending is None:
            raise RuntimeError("no writable receive buffer is reserved")

        if not 0 <= nbytes <= len(pending):
            raise ValueError("nbytes is outside the reserved receive buffer")

        self._end += nbytes
        pending.release()
        self._pending_buffer = None
        if not self and len(self._data) > 1024 * 1024:
            # No bytes were received: do not pin a giant requested capacity.
            self._data = bytearray()
            self._start = self._end = 0

    def __iadd__(self, byteslike: Union[bytes, bytearray]) -> "ReceiveBuffer":
        if self._pending_buffer is not None:
            raise RuntimeError("cannot append while a writable buffer is reserved")

        self._reserve(len(byteslike))
        end = self._end + len(byteslike)
        self._data[self._end : end] = byteslike
        self._end = end
        return self

    def __bool__(self) -> bool:
        return self._end > self._start

    def __len__(self) -> int:
        return self._end - self._start

    def __bytes__(self) -> bytes:
        return bytes(self._data[self._start : self._end])

    def _extract(self, count: int) -> bytearray:
        """Remove up to count bytes without resizing the underlying bytearray."""
        if count < 0:
            count = max(0, len(self) + count)
        count = min(count, len(self))
        out = self._data[self._start : self._start + count]
        self._start += count
        if self._start == self._end:
            self._start = self._end = 0
            if len(self._data) > 1024 * 1024:
                # A single oversized read must not permanently retain
                # that allocation across a keep-alive connection.
                self._data = bytearray()

        self._next_line_search = self._start
        self._multiple_lines_search = self._start
        return out

    def maybe_extract_at_most(self, count: int) -> Optional[bytearray]:
        """Extract up to count bytes from the buffer."""
        if not self or count == 0:
            return None

        return self._extract(count)

    def maybe_extract_next_line(self) -> Optional[bytearray]:
        """Extract the first completed CRLF-terminated line."""
        search_start_index = max(self._start, self._next_line_search - 1)
        partial_idx = self._data.find(b"\r\n", search_start_index, self._end)
        if partial_idx == -1:
            self._next_line_search = self._end
            return None

        return self._extract(partial_idx + 2 - self._start)

    def maybe_extract_lines(self) -> Optional[List[bytearray]]:
        """Extract all lines up to the next empty line."""
        if self and self._data[self._start : self._start + 1] == b"\n":
            self._extract(1)
            return []

        if len(self) >= 2 and self._data[self._start : self._start + 2] == b"\r\n":
            self._extract(2)
            return []

        match = blank_line_regex.search(
            self._data, max(self._start, self._multiple_lines_search), self._end
        )
        if match is None:
            self._multiple_lines_search = max(self._start, self._end - 2)
            return None

        out = self._extract(match.end() - self._start)
        lines = out.split(b"\n")
        for line in lines:
            if line.endswith(b"\r"):
                del line[-1]

        assert lines[-2] == lines[-1] == b""
        del lines[-2:]
        return lines

    # In theory we should wait until `\r\n` before starting to validate
    # incoming data. However it's interesting to detect (very) invalid data
    # early given they might not even contain `\r\n` at all (hence only
    # timeout will get rid of them).
    # This is not a 100% effective detection but more of a cheap sanity check
    # allowing for early abort in some useful cases.
    # This is especially interesting when peer is messing up with HTTPS and
    # sent us a TLS stream where we were expecting plain HTTP given all
    # versions of TLS so far start handshake with a 0x16 message type code.
    def is_next_line_obviously_invalid_request_line(self) -> bool:
        # Fast-fail common invalid request-line prefixes, including TLS
        # handshake bytes mistakenly sent to a cleartext HTTP socket.
        return bool(self) and self._data[self._start] < 0x21
