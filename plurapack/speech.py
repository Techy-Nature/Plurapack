from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from .storage import Member


class SpeechBackend(Protocol):
    async def synthesize(self, text: str, member: Member) -> bytes: ...


@dataclass(frozen=True)
class SpeechJob:
    channel_id: str
    proxy_message_id: str
    text: str
    member: Member


class SpeechQueue:
    """Bounded asynchronous queue; delivery is supplied by the platform adapter."""
    def __init__(self, backend: SpeechBackend, deliver, limit: int = 8):
        self.backend, self.deliver = backend, deliver
        self.queue: asyncio.Queue[SpeechJob] = asyncio.Queue(maxsize=limit)
        self.cancelled: set[str] = set()

    def submit(self, job: SpeechJob) -> bool:
        if job.member.playback not in {"send", "both"}:
            return False
        try:
            self.queue.put_nowait(job)
            return True
        except asyncio.QueueFull:
            return False

    def cancel(self, proxy_message_id: str) -> None:
        self.cancelled.add(proxy_message_id)

    async def run_one(self) -> None:
        job = await self.queue.get()
        try:
            if job.proxy_message_id not in self.cancelled:
                audio = await self.backend.synthesize(job.text, job.member)
                if job.proxy_message_id not in self.cancelled:
                    await self.deliver(job, audio)
        finally:
            self.cancelled.discard(job.proxy_message_id)
            self.queue.task_done()


async def speech_worker(queue: SpeechQueue) -> None:
    """Run speech jobs forever without allowing one failed job to stop the worker."""
    while True:
        try:
            await queue.run_one()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Job inputs may contain private text/audio identifiers. Log only the
            # exception class, not its potentially sensitive message.
            logging.getLogger(__name__).error("Speech job failed (%s)", type(error).__name__)
