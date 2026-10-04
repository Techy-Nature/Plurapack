"""Shared authorization, validation, metadata, and storage for custom voices."""
from __future__ import annotations

import io
import re
import uuid
import wave
from collections.abc import Callable
from .storage import Member, Store, Voice
from .voice_storage import VoiceNotFoundError, VoiceStorage


class VoiceValidationError(ValueError):
    pass


class VoiceService:
    def __init__(self, store: Store, storage: VoiceStorage, max_bytes: int = 25 * 1024 * 1024,
                 max_seconds: float = 30):
        self.store, self.storage, self.max_bytes, self.max_seconds = (
            store, storage, max_bytes, max_seconds)
        self._delete_listeners: list[Callable[[str], None]] = []

    def add_delete_listener(self, listener: Callable[[str], None]) -> None:
        """Register process-local cleanup for synchronized reference caches."""
        self._delete_listeners.append(listener)

    def _notify_deleted(self, storage_id: str) -> None:
        for listener in self._delete_listeners:
            try:
                listener(storage_id)
            except Exception:
                # Cache invalidation is best-effort and must not make an
                # otherwise completed storage/database deletion look failed.
                pass

    def _member(self, account_id: str, member_id: str) -> Member:
        member = self.store.member_selected(account_id, member_id)
        if member is None:
            raise PermissionError("Member not found or permission denied.")
        return member

    def _wav(self, audio: bytes) -> bytes:
        if not audio:
            raise VoiceValidationError("Voice audio cannot be empty.")
        if len(audio) > self.max_bytes:
            raise VoiceValidationError(f"Voice audio exceeds the {self.max_bytes} byte limit.")
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                if source.getnframes() < 1 or source.getnchannels() < 1 or source.getframerate() < 1:
                    raise VoiceValidationError("Voice audio is empty or invalid.")
                if source.getnframes() / source.getframerate() > self.max_seconds:
                    raise VoiceValidationError(
                        f"Voice reference must be {self.max_seconds:g} seconds or shorter.")
        except (wave.Error, EOFError) as error:
            raise VoiceValidationError("Voice audio must be a valid WAV file.") from error
        return audio

    def upload_member_voice(self, account_id: str, member_id: str, name: str,
                            audio: bytes, *, make_default: bool = False) -> Voice:
        self._member(account_id, member_id)
        name = name.strip()
        if not name or len(name) > 80:
            raise VoiceValidationError("Voice name must contain 1 to 80 characters.")
        audio = self._wav(audio)
        voice_id = str(uuid.uuid4())
        stored = self.storage.put_voice(voice_id, audio)
        voice: Voice | None = None
        try:
            voice = self.store.create_member_voice(account_id, member_id, voice_id, name,
                                                   stored.storage_id, stored.provider, make_default)
            if voice.is_default:
                current = self._member(account_id, member_id)
                self.store.configure_voice(account_id, member_id, f"{voice.storage_id}.wav",
                                           current.voice_settings, current.playback)
            return voice
        except Exception:
            if voice is not None:
                try:
                    self.store.delete_member_voice(account_id, member_id, voice.id)
                except Exception:
                    pass
            try:
                self.storage.delete_voice(voice_id)
            except Exception:
                pass
            raise

    def list_member_voices(self, account_id: str, member_id: str) -> list[Voice]:
        self._member(account_id, member_id)
        return self.store.member_voices(member_id)

    def select_member_voice(self, account_id: str, member_id: str, selector: str) -> Voice:
        voices = self.list_member_voices(account_id, member_id)
        matches = [voice for voice in voices
                   if voice.id == selector or voice.name.casefold() == selector.casefold()]
        if len(matches) != 1:
            raise ValueError("Voice not found; use its exact name or UUID.")
        return matches[0]

    def rename_member_voice(self, account_id: str, member_id: str, voice_id: str, name: str) -> Voice:
        self._member(account_id, member_id)
        return self.store.update_member_voice(account_id, member_id, voice_id, name=name)

    def set_default_member_voice(self, account_id: str, member_id: str, voice_id: str) -> Voice:
        member = self._member(account_id, member_id)
        voice = self.store.update_member_voice(account_id, member_id, voice_id, make_default=True)
        self.store.configure_voice(account_id, member_id, f"{voice.storage_id}.wav",
                                   member.voice_settings, member.playback)
        return voice

    def select_generic_voice(self, account_id: str, member_id: str, filename: str) -> Member:
        member = self._member(account_id, member_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.wav", filename):
            raise ValueError("Generic voice must be a safe WAV filename.")
        return self.store.configure_voice(account_id, member.id, filename,
                                          member.voice_settings, member.playback)

    def delete_member_voice(self, account_id: str, member_id: str, voice_id: str) -> Voice:
        # TODO: also purge the synchronized Chatterbox copy when upstream offers
        # a supported authenticated reference-deletion endpoint.
        member = self._member(account_id, member_id)
        voice = self.store.member_voice(account_id, member_id, voice_id)
        was_selected = member.voice_reference == f"{voice.storage_id}.wav"
        self.store.delete_member_voice(account_id, member_id, voice_id)
        try:
            self.storage.delete_voice(voice.storage_id)
        except VoiceNotFoundError:
            pass
        except Exception:
            self.store.restore_member_voice(voice)
            raise
        if was_selected:
            member = self._member(account_id, member_id)
            replacement = next((item for item in self.store.member_voices(member_id)
                                if item.is_default), None)
            reference = f"{replacement.storage_id}.wav" if replacement else None
            self.store.configure_voice(account_id, member_id, reference,
                                       member.voice_settings,
                                       member.playback if replacement else "off")
        self._notify_deleted(voice.storage_id)
        return voice

    def get_member_voice(self, account_id: str, member_id: str, voice_id: str) -> bytes:
        voice = self.store.member_voice(account_id, member_id, voice_id)
        return self.storage.get_voice(voice.storage_id)

    def resolve_member_voice(self, member: Member) -> tuple[str, bytes] | None:
        custom = next((voice for voice in self.store.member_voices(member.id) if voice.is_default), None)
        if selected_voice_type(self.store, member) != "custom" or custom is None:
            return None
        return f"{custom.storage_id}.wav", self.storage.get_voice(custom.storage_id)

    def delete_member(self, account_id: str, member_id: str) -> Member:
        member = self._member(account_id, member_id)
        for voice in self.store.member_voices(member.id):
            try:
                self.storage.delete_voice(voice.storage_id)
            except VoiceNotFoundError:
                pass
            self._notify_deleted(voice.storage_id)
        return self.store.delete_member(account_id, member.id)

    def delete_system(self, account_id: str, confirmation: str) -> str:
        system_id = self.store.system_for(account_id)
        if system_id is None or confirmation != system_id:
            raise PermissionError("System not found or confirmation does not match.")
        for member in self.store.members_for_system(system_id):
            for voice in self.store.member_voices(member.id):
                try:
                    self.storage.delete_voice(voice.storage_id)
                except VoiceNotFoundError:
                    pass
                self._notify_deleted(voice.storage_id)
        return self.store.delete_system(account_id, confirmation)


def voice_json(voice: Voice) -> dict:
    return {"id": voice.id, "memberId": voice.member_id, "name": voice.name,
            "isDefault": voice.is_default, "type": "custom"}


def selected_voice_type(store: Store, member: Member) -> str | None:
    """Identify the selected source without treating stored custom voices as selected."""
    if member.voice_reference is None:
        return None
    custom = next((voice for voice in store.member_voices(member.id) if voice.is_default), None)
    return "custom" if custom and member.voice_reference == f"{custom.storage_id}.wav" else "generic"


def resolve_voice_reference(store: Store, storage: VoiceStorage, member: Member) -> bytes | str:
    """Resolve custom UUID audio, while preserving legacy/generic filename selections."""
    custom = next((voice for voice in store.member_voices(member.id) if voice.is_default), None)
    if custom is not None and selected_voice_type(store, member) == "custom":
        return storage.get_voice(custom.storage_id)
    if member.voice_reference:
        return member.voice_reference
    raise ValueError("Speech voice reference is not configured.")
