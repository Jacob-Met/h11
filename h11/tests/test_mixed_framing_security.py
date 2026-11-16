from typing import Type

import pytest

import h11
from h11 import CLIENT, SERVER, Connection, EndOfMessage, RemoteProtocolError, Request
from h11._util import Sentinel


@pytest.mark.parametrize("role", [CLIENT, SERVER])
@pytest.mark.parametrize("order", [False, True])
def test_received_mixed_cl_te_rejected(role: Type[Sentinel], order: bool) -> None:
    framing = (
        b"Content-Length: 6\r\nTransfer-Encoding: chunked\r\n"
        if not order
        else b"Transfer-Encoding: chunked\r\nContent-Length: 6\r\n"
    )
    conn = Connection(role)
    if role is CLIENT:
        conn.send(Request(method="GET", target="/", headers=[("Host", "e.test")]))
        conn.send(EndOfMessage())
        wire = b"HTTP/1.1 200 OK\r\n" + framing + b"\r\n0\r\n\r\n"
    else:
        wire = b"POST / HTTP/1.1\r\nHost: e.test\r\n" + framing + b"\r\n0\r\n\r\n"
    conn.receive_data(wire)
    with pytest.raises(RemoteProtocolError):
        conn.next_event()


@pytest.mark.parametrize("order", [False, True])
def test_sent_mixed_cl_te_drops_overridden_length(order: bool) -> None:
    headers = [
        ("Host", "e.test"),
        ("Content-Length", "6"),
        ("Transfer-Encoding", "chunked"),
    ]
    if order:
        headers[1], headers[2] = headers[2], headers[1]
    conn = Connection(CLIENT)
    request = Request(method="POST", target="/", headers=headers)
    wire = conn.send(request)
    assert b"Transfer-Encoding: chunked\r\n" in wire
    assert b"Content-Length:" not in wire


@pytest.mark.parametrize("status", [204, 304])
def test_sent_bodyless_mixed_framing_removes_content_length(status: int) -> None:
    from h11 import Response

    conn = Connection(SERVER)
    conn.receive_data(b"GET / HTTP/1.1\r\nHost: e.test\r\n\r\n")
    conn.next_event()
    conn.next_event()
    wire = conn.send(
        Response(
            status_code=status,
            headers=[
                ("Transfer-Encoding", "chunked"),
                ("Content-Length", "6"),
            ],
        )
    )
    assert b"Content-Length:" not in wire
    if status == 204:
        assert b"Transfer-Encoding:" not in wire


def test_sent_informational_mixed_framing_removes_content_length() -> None:
    from h11 import InformationalResponse

    conn = Connection(SERVER)
    conn.receive_data(b"GET / HTTP/1.1\r\nHost: e.test\r\n\r\n")
    conn.next_event()
    conn.next_event()
    wire = conn.send(
        InformationalResponse(
            status_code=103,
            headers=[
                ("Transfer-Encoding", "chunked"),
                ("Content-Length", "6"),
            ],
        )
    )
    assert b"Content-Length:" not in wire
    assert b"Transfer-Encoding:" not in wire


@pytest.mark.parametrize("role", [CLIENT, SERVER])
def test_incremental_mixed_framing_rejected(role: Type[Sentinel]) -> None:
    conn = Connection(role)
    if role is CLIENT:
        conn.send(Request(method="GET", target="/", headers=[("Host", "e.test")]))
        conn.send(EndOfMessage())
        wire = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 4\r\n\r\n"
    else:
        wire = b"POST / HTTP/1.1\r\nHost: e.test\r\nTransfer-Encoding: chunked\r\nContent-Length: 4\r\n\r\n"
    for char in wire:
        conn.receive_data(bytes([char]))
    with pytest.raises(RemoteProtocolError):
        conn.next_event()
    assert conn.their_state is h11.ERROR


def test_sent_switch_mixed_framing_drops_content_length() -> None:
    from h11 import InformationalResponse

    conn = Connection(SERVER)
    conn.receive_data(
        b"GET / HTTP/1.1\r\nHost: e.test\r\nConnection: upgrade\r\nUpgrade: websocket\r\n\r\n"
    )
    conn.next_event()
    conn.next_event()
    wire = conn.send(
        InformationalResponse(
            status_code=101,
            headers=[
                ("Connection", "upgrade"),
                ("Upgrade", "websocket"),
                ("Transfer-Encoding", "chunked"),
                ("Content-Length", "4"),
            ],
        )
    )
    assert b"Content-Length:" not in wire
    assert b"Transfer-Encoding:" not in wire


def test_successful_connect_strips_body_framing() -> None:
    from h11 import Response

    conn = Connection(SERVER)
    conn.receive_data(b"CONNECT e.test:443 HTTP/1.1\r\nHost: e.test\r\n\r\n")
    conn.next_event()
    conn.next_event()
    wire = conn.send(
        Response(
            status_code=200,
            headers=[
                ("Transfer-Encoding", "chunked"),
                ("Content-Length", "6"),
            ],
        )
    )
    assert b"Content-Length:" not in wire
    assert b"Transfer-Encoding:" not in wire
