import re
from typing import Tuple

import pytest

from .._receivebuffer import ReceiveBuffer


def test_receivebuffer() -> None:
    b = ReceiveBuffer()
    assert not b
    assert len(b) == 0
    assert bytes(b) == b""

    b += b"123"
    assert b
    assert len(b) == 3
    assert bytes(b) == b"123"

    assert bytes(b) == b"123"

    assert b.maybe_extract_at_most(2) == b"12"
    assert b
    assert len(b) == 1
    assert bytes(b) == b"3"

    assert bytes(b) == b"3"

    assert b.maybe_extract_at_most(10) == b"3"
    assert bytes(b) == b""

    assert b.maybe_extract_at_most(10) is None
    assert not b

    ################################################################
    # maybe_extract_until_next
    ################################################################

    b += b"123\n456\r\n789\r\n"

    assert b.maybe_extract_next_line() == b"123\n456\r\n"
    assert bytes(b) == b"789\r\n"

    assert b.maybe_extract_next_line() == b"789\r\n"
    assert bytes(b) == b""

    b += b"12\r"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b"12\r"

    b += b"345\n\r"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b"12\r345\n\r"

    # here we stopped at the middle of b"\r\n" delimiter

    b += b"\n6789aaa123\r\n"
    assert b.maybe_extract_next_line() == b"12\r345\n\r\n"
    assert b.maybe_extract_next_line() == b"6789aaa123\r\n"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b""

    ################################################################
    # maybe_extract_lines
    ################################################################

    b += b"123\r\na: b\r\nfoo:bar\r\n\r\ntrailing"
    lines = b.maybe_extract_lines()
    assert lines == [b"123", b"a: b", b"foo:bar"]
    assert bytes(b) == b"trailing"

    assert b.maybe_extract_lines() is None

    b += b"\r\n\r"
    assert b.maybe_extract_lines() is None

    assert b.maybe_extract_at_most(100) == b"trailing\r\n\r"
    assert not b

    # Empty body case (as happens at the end of chunked encoding if there are
    # no trailing headers, e.g.)
    b += b"\r\ntrailing"
    assert b.maybe_extract_lines() == []
    assert bytes(b) == b"trailing"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            (
                b"HTTP/1.1 200 OK\r\n",
                b"Content-type: text/plain\r\n",
                b"Connection: close\r\n",
                b"\r\n",
                b"Some body",
            ),
            id="with_crlf_delimiter",
        ),
        pytest.param(
            (
                b"HTTP/1.1 200 OK\n",
                b"Content-type: text/plain\n",
                b"Connection: close\n",
                b"\n",
                b"Some body",
            ),
            id="with_lf_only_delimiter",
        ),
        pytest.param(
            (
                b"HTTP/1.1 200 OK\n",
                b"Content-type: text/plain\r\n",
                b"Connection: close\n",
                b"\n",
                b"Some body",
            ),
            id="with_mixed_crlf_and_lf",
        ),
    ],
)
def test_receivebuffer_for_invalid_delimiter(data: Tuple[bytes]) -> None:
    b = ReceiveBuffer()

    for line in data:
        b += line

    lines = b.maybe_extract_lines()

    assert lines == [
        b"HTTP/1.1 200 OK",
        b"Content-type: text/plain",
        b"Connection: close",
    ]
    assert bytes(b) == b"Some body"


def test_writable_buffer_incremental_parsing() -> None:
    buf = ReceiveBuffer()
    view = buf.get_buffer(5)
    assert len(view) >= 5
    view[:5] = b"foo\r\n"
    buf.buffer_updated(5)
    with pytest.raises(ValueError):
        view[0]

    assert len(buf) == 5
    assert bytes(buf) == b"foo\r\n"
    assert buf.maybe_extract_next_line() == b"foo\r\n"
    assert not buf

    # A zero-byte update is not an EOF signal.
    view = buf.get_buffer()
    buf.buffer_updated(0)
    assert not buf

    view = buf.get_buffer(1)
    view[:1] = b"A"
    buf.buffer_updated(1)
    assert buf.maybe_extract_next_line() is None

    view = buf.get_buffer(2)
    view[:2] = b"\r\n"
    buf.buffer_updated(2)
    assert buf.maybe_extract_next_line() == b"A\r\n"
    assert not buf


def test_writable_buffer_pending_validation_and_detached_views() -> None:
    buf = ReceiveBuffer()
    with pytest.raises(RuntimeError, match="no writable"):
        buf.buffer_updated(1)

    with pytest.raises(ValueError, match="sizehint"):
        buf.get_buffer(-2)

    view = buf.get_buffer(10)
    with pytest.raises(RuntimeError, match="previous writable"):
        buf.get_buffer()

    with pytest.raises(RuntimeError, match="cannot append"):
        buf += b"abc"

    for invalid_count in (-1, len(view) + 1):
        with pytest.raises(ValueError, match="nbytes"):
            buf.buffer_updated(invalid_count)

    view[:3] = b"abc"
    # A caller that retains another view must not prevent a future buffer grow.
    retained = view[:3]
    capacity = len(view)
    buf.buffer_updated(3)
    buf += b"x" * (capacity + 1)
    assert bytes(buf) == b"abc" + b"x" * (capacity + 1)
    assert bytes(retained) == b"abc"
    retained.release()

    assert buf.maybe_extract_at_most(3) == b"abc"
    assert buf.maybe_extract_at_most(100_000) == b"x" * (capacity + 1)
    assert not buf


def test_writable_buffer_http_headers_and_trailing_body() -> None:
    buf = ReceiveBuffer()
    buf += b"GET /\r\nHost: "
    assert buf.maybe_extract_lines() is None
    view = buf.get_buffer()
    payload = b"example.test\r\n\r\nhi"
    view[: len(payload)] = payload
    buf.buffer_updated(len(payload))
    assert buf.maybe_extract_lines() == [b"GET /", b"Host: example.test"]
    assert bytes(buf) == b"hi"
    assert buf.maybe_extract_at_most(10) == b"hi"
    assert not buf


def test_writable_buffer_legacy_zero_and_negative_counts() -> None:
    buf = ReceiveBuffer()
    buf += b"abcd"
    assert buf.maybe_extract_at_most(0) is None
    assert bytes(buf) == b"abcd"
    assert buf.maybe_extract_at_most(-1) == b"abc"
    assert bytes(buf) == b"d"
    assert buf.maybe_extract_at_most(-10) == b""
    assert bytes(buf) == b"d"
    assert buf.maybe_extract_at_most(10) == b"d"


def test_writable_buffer_empty_lf_header_separator() -> None:
    buf = ReceiveBuffer()
    view = buf.get_buffer(1)
    view[0] = 10
    buf.buffer_updated(1)
    assert buf.maybe_extract_lines() == []
    assert not buf


def test_writable_buffer_does_not_parse_stale_capacity() -> None:
    buf = ReceiveBuffer()
    buf += b"\n"
    assert buf.maybe_extract_lines() == []
    assert buf.maybe_extract_lines() is None

    buf += b"\r\n"
    assert buf.maybe_extract_lines() == []
    # This new lone CR must not be confused with the old LF still in
    # the unused part of the allocated writable capacity.
    buf += b"\r"
    assert buf.maybe_extract_lines() is None
    buf += b"\n"
    assert buf.maybe_extract_lines() == []
    assert not buf


def test_writable_buffer_releases_oversized_capacity_after_consumption() -> None:
    buf = ReceiveBuffer()
    view = buf.get_buffer(2 * 1024 * 1024)
    view[:3] = b"abc"
    buf.buffer_updated(3)
    assert buf.maybe_extract_at_most(3) == b"abc"
    assert not buf
    assert len(buf._data) <= 1024 * 1024

    view = buf.get_buffer(2 * 1024 * 1024)
    buf.buffer_updated(0)
    assert not buf
    assert len(buf._data) <= 1024 * 1024
