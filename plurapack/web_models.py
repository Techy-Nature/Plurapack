"""Validated public representations for the dashboard API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SystemPatch(APIModel):
    display_name: str | None = Field(None, alias="displayName", min_length=1, max_length=80)
    description: str | None = Field(None, max_length=1000)
    logo: HttpUrl | None = None
    system_tag: str | None = Field(None, alias="tag", max_length=32)
    show_system_tag: bool | None = Field(None, alias="showSystemTag")


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
    default_form_id: str | None = Field(None, alias="defaultFormId", pattern=r"^[0-9a-f]{5}$")
    playback: Literal["off", "local", "send", "both"] | None = None
    voice_settings: dict[str, Any] | None = Field(None, alias="voiceSettings")


class FormCreate(APIModel):
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    picture: HttpUrl | None = None
    soma: str = Field("", max_length=1000)
    pronouns: str | None = Field(None, max_length=64)
    prefix: str = Field("", max_length=32)
    suffix: str = Field("", max_length=32)


class FormPatch(APIModel):
    display_name: str | None = Field(None, alias="displayName", min_length=1, max_length=80)
    picture: HttpUrl | None = None
    soma: str | None = Field(None, max_length=1000)
    pronouns: str | None = Field(None, max_length=64)
    prefix: str | None = Field(None, max_length=32)
    suffix: str | None = Field(None, max_length=32)


class FrontUpdate(APIModel):
    member_id: str | None = Field(None, alias="memberId", pattern=r"^[0-9a-f]{5}$")
    form_id: str | None = Field(None, alias="formId", pattern=r"^[0-9a-f]{5}$")
