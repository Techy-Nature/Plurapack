from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from .storage import Member


class SpeechBackend(Protocol):
    async def synthesize(self, text: str, member: Member) -> bytes: ...


@dataclass(frozen=True)
class SpeechPart:
    text: str
    style: str = "normal"


def speech_parts(text: str, member: Member) -> tuple[SpeechPart, ...]:
    """Interpret the small Markdown subset used for optional expressive speech.

    Single-star spans are stage directions and are omitted. Double-star spans
    are emphasized. Strikethrough follows the member's explicit preference.
    Delimiters are never spoken. This intentionally is not a full Markdown
    renderer, which keeps unmatched punctuation from silently eating speech.
    """
    if not member.speech_formatting:
        return (SpeechPart(text),)
    parts: list[SpeechPart] = []
    buffer: list[str] = []

    def add(value: str, style: str = "normal") -> None:
        if not value:
            return
        if parts and parts[-1].style == style:
            parts[-1] = SpeechPart(parts[-1].text + value, style)
        else:
            parts.append(SpeechPart(value, style))

    index = 0
    while index < len(text):
        marker = next((item for item in ("**", "~~", "*") if text.startswith(item, index)), None)
        if marker is None:
            buffer.append(text[index])
            index += 1
            continue
        end = text.find(marker, index + len(marker))
        if end < 0:
            buffer.append(marker)
            index += len(marker)
            continue
        add("".join(buffer))
        buffer.clear()
        value = text[index + len(marker):end]
        if marker == "**":
            add(value, "emphasis")
        elif marker == "~~" and member.strikethrough_speech != "omit":
            add(value, member.strikethrough_speech)
        # A single-star action and omitted strikethrough deliberately add nothing.
        index = end + len(marker)
    add("".join(buffer))
    # Avoid awkward gaps left by omitted actions without altering spoken words.
    return tuple(SpeechPart(" ".join(part.text.split()), part.style)
                 for part in parts if part.text.strip())


@dataclass(frozen=True)
class SpeechJob:
    channel_id: str
    proxy_message_id: str
    text: str
    member: Member
    generation: int = 0
    account_id: str = ""


class SpeechQueue:
    """Bounded asynchronous queue; delivery is supplied by the platform adapter."""
    def __init__(self, backend: SpeechBackend, deliver, limit: int = 8,
                 deliver_local=None, invalidate_local=None):
        self.backend, self.deliver = backend, deliver
        self.deliver_local, self.invalidate_local = deliver_local, invalidate_local
        self.queue: asyncio.Queue[SpeechJob] = asyncio.Queue(maxsize=limit)
        self._generation = 0
        self._current: dict[str, int] = {}
        self._pending: dict[str, int] = {}

    def submit(self, job: SpeechJob) -> bool:
        if job.member.playback not in {"send", "local", "both"}:
            return False
        self._generation += 1
        job = SpeechJob(job.channel_id, job.proxy_message_id, job.text, job.member,
                        generation=self._generation, account_id=job.account_id)
        try:
            self.queue.put_nowait(job)
            self._current[job.proxy_message_id] = job.generation
            self._pending[job.proxy_message_id] = self._pending.get(job.proxy_message_id, 0) + 1
            return True
        except asyncio.QueueFull:
            return False

    def cancel(self, proxy_message_id: str) -> None:
        """Invalidate every queued or synthesizing generation for a proxy."""
        self._current.pop(proxy_message_id, None)
        if self.invalidate_local:
            try:
                self.invalidate_local(proxy_message_id)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "Could not invalidate browser speech (%s)", type(error).__name__)

    def is_current(self, job: SpeechJob) -> bool:
        return self._current.get(job.proxy_message_id) == job.generation

    async def run_one(self) -> None:
        job = await self.queue.get()
        try:
            if self.is_current(job):
                parts = speech_parts(job.text, job.member)
                if not parts:
                    return
                styled = getattr(self.backend, "synthesize_styled", None)
                if job.member.speech_formatting and styled is not None:
                    audio = await styled(parts, job.member)
                else:
                    audio = await self.backend.synthesize(" ".join(part.text for part in parts), job.member)
                # Cancellation cannot necessarily stop an HTTP request, so the
                # generation is checked again after synthesis completes.
                if self.is_current(job):
                    await self._route(job, audio)
        finally:
            remaining = self._pending[job.proxy_message_id] - 1
            if remaining:
                self._pending[job.proxy_message_id] = remaining
            else:
                self._pending.pop(job.proxy_message_id, None)
                if self._current.get(job.proxy_message_id) == job.generation:
                    self._current.pop(job.proxy_message_id, None)
            self.queue.task_done()

    async def _route(self, job: SpeechJob, audio: bytes) -> None:
        """Route one synthesis result, isolating failures between destinations."""
        destinations = []
        if job.member.playback in {"send", "both"}:
            destinations.append(("Stoat", self.deliver))
        if job.member.playback in {"local", "both"} and self.deliver_local:
            destinations.append(("browser", self.deliver_local))
        for name, destination in destinations:
            try:
                result = destination(job, audio)
                if hasattr(result, "__await__"):
                    await result
            except Exception as error:
                logging.getLogger(__name__).error(
                    "%s speech delivery failed (%s)", name, type(error).__name__)


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
