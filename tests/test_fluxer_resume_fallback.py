"""The Fluxer gateway stops retrying a resume address that no longer answers.

These tests drive fluxer.py's real Gateway class with a fake WebSocket session,
so they check the library's own reconnect path, not a copy of it.
"""
import asyncio
import json
import logging

import aiohttp
import pytest

fluxer_gateway = pytest.importorskip("fluxer.gateway")

import plurapack.fluxer_bot as fluxer_bot
from plurapack.fluxer_bot import (
    FluxerGatewayStatus,
    install_resume_fallback,
    watch_fluxer_gateway,
)

MAIN_URL = "wss://gateway.example.test?v=1&encoding=json"
RESUME_URL = "wss://resume-node.example.test"


class FakeMessage:
    def __init__(self, kind, data=None):
        self.type = kind
        self.data = data


class FakeSocket:
    """Plays a list of gateway frames, then closes like a dropped connection."""

    def __init__(self, frames, close_code=1006):
        self._frames = list(frames)
        self.close_code = close_code
        self.closed = False
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Let fluxer.py's payload tasks run between frames, as a real socket would.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def send_str(self, raw):
        self.sent.append(json.loads(raw))

    async def close(self, code=1000):
        self.closed = True

    def exception(self):
        return None


class FakeSession:
    """Answers ws_connect per URL: an exception, a hang, or a FakeSocket."""

    def __init__(self, answers):
        self.answers = answers
        self.urls = []

    async def ws_connect(self, url, **kwargs):
        self.urls.append(url)
        base = url.split("?")[0]
        answer = self.answers[base]
        if answer == "hang":
            await asyncio.Event().wait()
        if isinstance(answer, BaseException):
            raise answer
        return answer


def hello_frame():
    # A huge heartbeat interval keeps the heartbeat task asleep for the test.
    return FakeMessage(aiohttp.WSMsgType.TEXT,
                       json.dumps({"op": 10, "d": {"heartbeat_interval": 10_000_000}}))


async def _no_dispatch(event, data):
    return None


def make_gateway(session):
    gateway = fluxer_gateway.Gateway(http_client=None, token="token", intents=0,
                                     dispatch=_no_dispatch)
    gateway._session = session
    gateway._session_id = "session-1"
    gateway._sequence = 41
    gateway._resume_gateway_url = RESUME_URL
    assert install_resume_fallback(gateway)
    return gateway


async def test_refused_resume_address_falls_back_to_main_gateway(capsys):
    main_socket = FakeSocket([hello_frame()])
    session = FakeSession({
        RESUME_URL: aiohttp.ClientConnectionError("503 Service Unavailable"),
        MAIN_URL.split("?")[0]: main_socket,
    })
    gateway = make_gateway(session)

    with pytest.raises(aiohttp.ClientError):
        await gateway._connect_and_run(MAIN_URL)

    assert gateway._resume_gateway_url is None
    # The session itself is kept, so the main gateway is still asked to RESUME it.
    assert gateway._session_id == "session-1"
    assert capsys.readouterr().out == (
        "[Plurapack] Fluxer's resume address did not answer; "
        "the next attempt uses the main gateway.\n"
    )

    await gateway._connect_and_run(MAIN_URL)

    assert session.urls == [f"{RESUME_URL}?v=1&encoding=json", MAIN_URL]
    assert main_socket.sent[0]["op"] == 6  # RESUME
    assert main_socket.sent[0]["d"]["session_id"] == "session-1"
    gateway._stop_heartbeat()


async def test_resume_address_that_answers_is_kept(capsys):
    resume_socket = FakeSocket([hello_frame()])
    gateway = make_gateway(FakeSession({RESUME_URL: resume_socket}))

    await gateway._connect_and_run(MAIN_URL)

    assert gateway._resume_gateway_url == RESUME_URL
    assert resume_socket.sent[0]["op"] == 6
    assert "resume address" not in capsys.readouterr().out
    gateway._stop_heartbeat()


async def test_resume_address_closing_before_hello_falls_back():
    gateway = make_gateway(FakeSession({RESUME_URL: FakeSocket([], close_code=1006)}))

    await gateway._connect_and_run(MAIN_URL)

    assert gateway._resume_gateway_url is None


async def test_hung_connection_times_out_and_falls_back(monkeypatch):
    monkeypatch.setattr(fluxer_bot, "FLUXER_HELLO_TIMEOUT_SECONDS", 0.05)
    gateway = make_gateway(FakeSession({RESUME_URL: "hang"}))

    with pytest.raises(asyncio.TimeoutError):
        await gateway._connect_and_run(MAIN_URL)

    assert gateway._resume_gateway_url is None


async def test_hung_main_gateway_times_out_without_touching_session(monkeypatch):
    monkeypatch.setattr(fluxer_bot, "FLUXER_HELLO_TIMEOUT_SECONDS", 0.05)
    gateway = make_gateway(FakeSession({MAIN_URL.split("?")[0]: "hang"}))
    gateway._resume_gateway_url = None

    with pytest.raises(asyncio.TimeoutError):
        await gateway._connect_and_run(MAIN_URL)

    assert gateway._session_id == "session-1"


def test_install_is_idempotent_and_skips_unknown_gateways():
    gateway = fluxer_gateway.Gateway(http_client=None, token="t", intents=0,
                                     dispatch=_no_dispatch)
    assert install_resume_fallback(gateway)
    patched = type(gateway)
    assert install_resume_fallback(gateway)
    assert type(gateway) is patched
    assert isinstance(gateway, fluxer_gateway.Gateway)

    assert not install_resume_fallback(None)
    assert not install_resume_fallback(object())


def test_failed_attempt_line_names_the_reason(capsys):
    watch_fluxer_gateway()
    gateway_log = logging.getLogger("fluxer.gateway")

    gateway_log.warning("Gateway connection error: %s. Reconnecting in 5s...", TimeoutError())
    gateway_log.warning("Gateway connection error: %s. Reconnecting in 5s...",
                        "x" * (FluxerGatewayStatus.REASON_LIMIT + 50))

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[Plurapack] Fluxer reconnect attempt failed (TimeoutError); trying again in 5s."
    assert lines[1].endswith("...); trying again in 5s.")
    assert len(lines[1]) < FluxerGatewayStatus.REASON_LIMIT + 80
