from typing import Iterator, Tuple
from unittest.mock import ANY

import pytest

from .._connection import Connection
from .._events import EndOfMessage, Request, Response
from .._headers import (
    get_comma_header,
    has_expect_100_continue,
    Headers,
    normalize_and_validate,
    set_comma_header,
)
from .._state import CLIENT
from .._util import LocalProtocolError


def test_header_equality_unsupported_and_iterable_operands() -> None:
    headers = normalize_and_validate([("Host", "example.com")])
    assert not (headers == None)  # noqa: E711 - exercising ordinary equality
    assert not (headers == 42)
    assert headers == ANY
    assert headers == [(b"host", b"example.com")]
    assert headers == ((b"host", b"example.com"),)
    assert headers == iter([(b"host", b"example.com")])

    class OldStyleSequence:
        def __getitem__(self, index: int) -> Tuple[bytes, bytes]:
            return [(b"host", b"example.com")][index]

    assert headers == OldStyleSequence()

    class FailingIterator:
        def __iter__(self) -> Iterator[Tuple[bytes, bytes]]:
            raise TypeError("internal iterator failure")

    with pytest.raises(TypeError, match="internal iterator failure"):
        headers == FailingIterator()


def test_normalize_and_validate() -> None:
    assert normalize_and_validate([("foo", "bar")]) == [(b"foo", b"bar")]
    assert normalize_and_validate([(b"foo", b"bar")]) == [(b"foo", b"bar")]

    # no leading/trailing whitespace in names
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo ", "bar")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b" foo", "bar")])

    # no weird characters in names
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate([(b"foo bar", b"baz")])
    assert "foo bar" in str(excinfo.value)
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo\x00bar", b"baz")])
    # Not even 8-bit characters:
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo\xffbar", b"baz")])
    # And not even the control characters we allow in values:
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo\x01bar", b"baz")])

    # no return or NUL characters in values
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate([("foo", "bar\rbaz")])
    assert "bar\\rbaz" in str(excinfo.value)
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "bar\nbaz")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "bar\x00baz")])
    # no leading/trailing whitespace
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "barbaz  ")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "  barbaz")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "barbaz\t")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "\tbarbaz")])

    # content-length
    assert normalize_and_validate([("Content-Length", "1")]) == [
        (b"content-length", b"1")
    ]
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "asdf")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "1x")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "1"), ("Content-Length", "2")])
    assert normalize_and_validate(
        [("Content-Length", "0"), ("Content-Length", "0")]
    ) == [(b"content-length", b"0")]
    assert normalize_and_validate([("Content-Length", "0 , 0")]) == [
        (b"content-length", b"0")
    ]
    with pytest.raises(LocalProtocolError):
        normalize_and_validate(
            [("Content-Length", "1"), ("Content-Length", "1"), ("Content-Length", "2")]
        )
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "1 , 1,2")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "1" * 21)])  # 1 billion TB

    # transfer-encoding
    assert normalize_and_validate([("Transfer-Encoding", "chunked")]) == [
        (b"transfer-encoding", b"chunked")
    ]
    assert normalize_and_validate([("Transfer-Encoding", "cHuNkEd")]) == [
        (b"transfer-encoding", b"chunked")
    ]
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate([("Transfer-Encoding", "gzip")])
    assert excinfo.value.error_status_hint == 501  # Not Implemented
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate(
            [("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "gzip")]
        )
    assert excinfo.value.error_status_hint == 501  # Not Implemented


def test_get_set_comma_header() -> None:
    headers = normalize_and_validate(
        [
            ("Connection", "close"),
            ("whatever", "something"),
            ("connectiON", "fOo,, , BAR"),
        ]
    )

    assert get_comma_header(headers, b"connection") == [b"close", b"foo", b"bar"]

    headers = set_comma_header(headers, b"newthing", ["a", "b"])  # type: ignore

    with pytest.raises(LocalProtocolError):
        set_comma_header(headers, b"newthing", ["  a", "b"])  # type: ignore

    assert headers == [
        (b"connection", b"close"),
        (b"whatever", b"something"),
        (b"connection", b"fOo,, , BAR"),
        (b"newthing", b"a"),
        (b"newthing", b"b"),
    ]

    headers = set_comma_header(headers, b"whatever", ["different thing"])  # type: ignore

    assert headers == [
        (b"connection", b"close"),
        (b"connection", b"fOo,, , BAR"),
        (b"newthing", b"a"),
        (b"newthing", b"b"),
        (b"whatever", b"different thing"),
    ]


def test_has_100_continue() -> None:
    assert has_expect_100_continue(
        Request(
            method="GET",
            target="/",
            headers=[("Host", "example.com"), ("Expect", "100-continue")],
        )
    )
    assert not has_expect_100_continue(
        Request(method="GET", target="/", headers=[("Host", "example.com")])
    )
    # Case insensitive
    assert has_expect_100_continue(
        Request(
            method="GET",
            target="/",
            headers=[("Host", "example.com"), ("Expect", "100-Continue")],
        )
    )
    # Doesn't work in HTTP/1.0
    assert not has_expect_100_continue(
        Request(
            method="GET",
            target="/",
            headers=[("Host", "example.com"), ("Expect", "100-continue")],
            http_version="1.0",
        )
    )


@pytest.mark.parametrize("control", [b"\x0b", b"\x0c"])
def test_header_values_accept_fetch_allowed_controls(control: bytes) -> None:
    # Fetch's header-value definition excludes NUL, CR and LF, not VT or FF.
    value = b"before" + control + b"after"
    expected = [(b"x-test", value)]

    assert normalize_and_validate(expected) == expected
    assert normalize_and_validate([(b"x-test", control)]) == [(b"x-test", control)]
    assert Response(status_code=200, headers=expected).headers == expected

    client = Connection(CLIENT)
    client.send(Request(method="GET", target="/", headers=[("host", "example.com")]))
    client.send(EndOfMessage())
    client.receive_data(
        b"HTTP/1.1 200 OK\r\n" + b"X-Test: " + value + b"\r\nContent-Length: 0\r\n\r\n"
    )
    response = client.next_event()
    assert isinstance(response, Response)
    assert response.headers == [(b"x-test", value), (b"content-length", b"0")]
