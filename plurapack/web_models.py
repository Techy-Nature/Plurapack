"""Validated public representations for the dashboard API."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProxyTagUpdate(APIModel):
    prefix: str = Field(min_length=1, max_length=32)
    suffix: str = Field("", max_length=32)


class ProxyTagsUpdate(APIModel):
    proxy_tags: list[ProxyTagUpdate] = Field(alias="proxyTags", max_length=100)


class SystemPatch(APIModel):
    display_name: str | None = Field(None, alias="displayName", min_length=1, max_length=80)
    description: str | None = Field(None, max_length=1000)
    logo: HttpUrl | None = None
    system_tag: str | None = Field(None, alias="tag", max_length=32)
    show_system_tag: bool | None = Field(None, alias="showSystemTag")
    banner: HttpUrl | None = None


class MemberCreate(APIModel):
    name: str = Field(min_length=1, max_length=80)
    proxy: str | None = Field(None, max_length=64)
    prefix: str | None = Field(None, max_length=32)
    suffix: str = Field("", max_length=32)
    alias: str | None = Field(None, max_length=24, pattern=r"^[^\s:]+$")
    pronouns: str | None = Field(None, max_length=64)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    avatar: HttpUrl | None = None
    description: str | None = Field("", max_length=1000)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value

    @model_validator(mode="after")
    def explicit_proxy_required(self) -> "MemberCreate":
        if not self.prefix and not self.proxy:
            raise ValueError("proxy or prefix is required")
        return self


class MemberPatch(APIModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    prefix: str | None = Field(None, max_length=32)
    suffix: str | None = Field(None, max_length=32)
    alias: str | None = Field(None, max_length=24, pattern=r"^[^\s:]+$")
    pronouns: str | None = Field(None, max_length=64)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    avatar: HttpUrl | None = None
    description: str | None = Field(None, max_length=1000)
    banner: HttpUrl | None = None
    default_form_id: str | None = Field(None, alias="defaultFormId", pattern=r"^[0-9a-f]{5}$")
    playback: Literal["off", "local", "send", "both"] | None = None
    voice_settings: dict[str, Any] | None = Field(None, alias="voiceSettings")

    @field_validator("description", mode="before")
    @classmethod
    def null_description_clears_text(cls, value: Any) -> Any:
        # Dashboard form fields use null for an empty input. The storage column
        # is non-nullable, so treat an explicit null as clearing the text.
        return "" if value is None else value


class FormCreate(APIModel):
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    picture: HttpUrl | None = None
    soma: str = Field("", max_length=1000)
    pronouns: str | None = Field(None, max_length=64)
    prefix: str = Field("", max_length=32)
    suffix: str = Field("", max_length=32)
    banner: HttpUrl | None = None


class FormPatch(APIModel):
    display_name: str | None = Field(None, alias="displayName", min_length=1, max_length=80)
    picture: HttpUrl | None = None
    soma: str | None = Field(None, max_length=1000)
    pronouns: str | None = Field(None, max_length=64)
    prefix: str | None = Field(None, max_length=32)
    suffix: str | None = Field(None, max_length=32)
    banner: HttpUrl | None = None


class FrontUpdate(APIModel):
    member_id: str | None = Field(None, alias="memberId", pattern=r"^[0-9a-f]{5}$")
    form_id: str | None = Field(None, alias="formId", pattern=r"^[0-9a-f]{5}$")


class GroupCreate(APIModel):
    name: str = Field(min_length=1, max_length=80)
    alias: str = Field(min_length=1, max_length=24, pattern=r"^[^\s:]+$")
    avatar: HttpUrl | None = None

    @field_validator("name", "alias")
    @classmethod
    def group_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class GroupPatch(APIModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    alias: str | None = Field(None, min_length=1, max_length=24, pattern=r"^[^\s:]+$")
    avatar: HttpUrl | None = None

    @field_validator("name", "alias")
    @classmethod
    def group_text_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class GroupMemberUpdate(APIModel):
    member_ids: list[str] = Field(alias="memberIds")

    @field_validator("member_ids")
    @classmethod
    def valid_member_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("memberIds must not contain duplicates")
        if any(not re.fullmatch(r"[0-9a-f]{5}", value) for value in values):
            raise ValueError("memberIds must contain five-character member IDs")
        return values


class ActiveGroupUpdate(APIModel):
    group_id: str = Field(alias="groupId", pattern=r"^[0-9a-f]{8}$")
