"""Read-only, token-authenticated v1 API for outside dashboards and member viewers.

Requests without a token, or with a `read:public` token, only ever see what a
system has made public with `privacy`. A `read:private` token sees its own
system in full. Hidden things are left out entirely (or answered with 404)
so their existence is not revealed. Account IDs, link codes, voices, and
proxy-message records are never part of these responses.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .login import LoginStartLimiter
from .storage import ApiToken, Form, Group, Member, MemberPrivacy, Store, System

API_PREFIX = "/api/v1"
RATE_WINDOW = 60
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization",
    "Access-Control-Expose-Headers": "Retry-After",
    "Access-Control-Max-Age": "600",
}


@dataclass(frozen=True)
class Viewer:
    token: ApiToken | None

    def full_access(self, system_id: str) -> bool:
        return (self.token is not None and self.token.scope == "read:private"
                and self.token.system_id == system_id)


def api_rate_limiter() -> LoginStartLimiter:
    return LoginStartLimiter(int(os.getenv("PLURAPACK_API_RATE_LIMIT", "120")), RATE_WINDOW)


def add_cors(response: Response) -> Response:
    response.headers.update(CORS_HEADERS)
    return response


def public_form_json(store: Store, form: Form, hidden: frozenset[str] | set[str],
                     full: bool) -> dict[str, Any]:
    data = {"id": form.id, "memberId": form.member_id, "displayName": form.display_name,
            "avatar": None if "avatar" in hidden else form.avatar,
            "banner": None if "banner" in hidden else form.banner,
            "pronouns": None if "pronouns" in hidden else form.pronouns,
            "soma": None if "description" in hidden else form.soma}
    if full:
        data["proxyTags"] = [tag.__dict__ for tag in store.proxy_tags(form_id=form.id)]
    return data


def public_member_json(store: Store, member: Member, privacy: MemberPrivacy,
                       full: bool) -> dict[str, Any]:
    hidden = frozenset() if full else privacy.hidden_fields

    def shown(field: str, value: Any) -> Any:
        return None if field in hidden else value

    data = {"id": member.id, "systemId": member.system_id, "name": member.name,
            "pronouns": shown("pronouns", member.pronouns),
            "description": shown("description", member.description),
            "avatar": shown("avatar", member.avatar), "banner": shown("banner", member.banner),
            "color": shown("color", member.color),
            "forms": ([] if "forms" in hidden else
                      [public_form_json(store, form, hidden, full)
                       for form in store.forms_for_member(member.id)])}
    if full:
        data.update({
            "alias": member.alias,
            "proxyTags": [tag.__dict__ for tag in store.proxy_tags(member_id=member.id)],
            "privacy": {"visibility": "public" if privacy.public else "private",
                        "hiddenFields": sorted(privacy.hidden_fields)},
        })
    return data


def public_group_json(group: Group, member_ids: list[str], public: bool, full: bool) -> dict[str, Any]:
    data = {"id": group.id, "systemId": group.system_id, "name": group.name,
            "alias": group.alias, "avatar": group.avatar, "banner": group.banner,
            "memberIds": member_ids}
    if full:
        data["privacy"] = {"visibility": "public" if public else "private"}
    return data


def create_public_api() -> APIRouter:
    """Build the v1 routes; the app supplies `state.store` and `state.api_rate_limiter`."""
    router = APIRouter(prefix=API_PREFIX, tags=["Public API v1"])

    def db(request: Request) -> Store:
        return request.app.state.store

    async def run(function: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(function, *args)

    async def viewer(request: Request, store: Store = Depends(db)) -> Viewer:
        # Never read the dashboard cookie here: these routes answer any origin.
        header = request.headers.get("authorization")
        token = None
        if header is not None:
            scheme, _, secret = header.partition(" ")
            if scheme.casefold() != "bearer" or not secret.strip():
                raise HTTPException(401, "Use an `Authorization: Bearer TOKEN` header",
                                    headers={"WWW-Authenticate": "Bearer"})
            token = await run(store.resolve_api_token, secret)
            if token is None:
                raise HTTPException(401, "That API token is invalid or was revoked",
                                    headers={"WWW-Authenticate": "Bearer"})
        client = f"token:{token.id}" if token else (
            f"ip:{request.client.host}" if request.client else "ip:unknown")
        if not request.app.state.api_rate_limiter.allow(client):
            raise HTTPException(429, "Too many API requests; try again shortly",
                                headers={"Retry-After": str(RATE_WINDOW)})
        return Viewer(token)

    def not_found() -> HTTPException:
        return HTTPException(404, "Resource not found")

    def visible_system(store: Store, system_id: str, who: Viewer) -> tuple[System, bool]:
        system = store.system_by_id(system_id)
        if system is None:
            raise not_found()
        full = who.full_access(system.id)
        if not full and not store.system_privacy(system.id).system_public:
            raise not_found()
        return system, full

    def visible_members(store: Store, system_id: str, full: bool
                        ) -> list[tuple[Member, MemberPrivacy]]:
        privacy = store.member_privacy_for_system(system_id)
        return [(member, privacy[member.id]) for member in store.members_for_system(system_id)
                if full or privacy[member.id].public]

    def visible_groups(store: Store, system_id: str, full: bool) -> list[dict[str, Any]]:
        member_ids = {member.id for member, _ in visible_members(store, system_id, full)}
        public_ids = store.public_group_ids(system_id)
        return [public_group_json(group,
                                  [member.id for member in store.public_group_members(group.id)
                                   if member.id in member_ids],
                                  group.id in public_ids, full)
                for group in store.groups_for_system(system_id) if full or group.id in public_ids]

    def system_payload(store: Store, system_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        data = {"id": system.id, "name": system.display_name, "description": system.description,
                "tag": system.system_tag, "logo": system.logo, "banner": system.banner,
                "memberCount": len(visible_members(store, system.id, full))}
        if full:
            settings = store.system_privacy(system.id)
            data["privacy"] = {"system": "public" if settings.system_public else "private",
                               "front": "public" if settings.front_public else "private"}
        return data

    def members_payload(store: Store, system_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        return {"members": [public_member_json(store, member, privacy, full)
                            for member, privacy in visible_members(store, system.id, full)]}

    def member_payload(store: Store, system_id: str, member_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        for member, privacy in visible_members(store, system.id, full):
            if member.id == member_id:
                return public_member_json(store, member, privacy, full)
        raise not_found()

    def groups_payload(store: Store, system_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        return {"groups": visible_groups(store, system.id, full)}

    def group_payload(store: Store, system_id: str, group_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        for group in visible_groups(store, system.id, full):
            if group["id"] == group_id:
                return group
        raise not_found()

    def fronters_payload(store: Store, system_id: str, who: Viewer) -> dict[str, Any]:
        system, full = visible_system(store, system_id, who)
        if not full and not store.system_privacy(system.id).front_public:
            raise not_found()
        front = store.system_front(system.id)
        if front is None:
            return {"fronters": []}
        privacy = store.member_privacy(front.member.id)
        # A private member fronting reads the same as nobody fronting.
        if not full and not privacy.public:
            return {"fronters": []}
        hide_form = not full and "forms" in privacy.hidden_fields
        return {"fronters": [{
            "member": public_member_json(store, front.member, privacy, full),
            "formId": None if hide_form or front.form is None else front.form.id,
        }]}

    @router.get("/me", summary="Describe the API token making this request")
    async def me(who: Viewer = Depends(viewer)) -> dict[str, Any]:
        if who.token is None:
            raise HTTPException(401, "This endpoint needs an API token",
                                headers={"WWW-Authenticate": "Bearer"})
        return {"systemId": who.token.system_id, "tokenId": who.token.id,
                "tokenName": who.token.name, "scope": who.token.scope}

    @router.get("/systems/{system_id}", summary="A system's public card")
    async def get_system(system_id: str, who: Viewer = Depends(viewer),
                         store: Store = Depends(db)) -> dict[str, Any]:
        return await run(system_payload, store, system_id, who)

    @router.get("/systems/{system_id}/members", summary="A system's visible members")
    async def list_members(system_id: str, who: Viewer = Depends(viewer),
                           store: Store = Depends(db)) -> dict[str, Any]:
        return await run(members_payload, store, system_id, who)

    @router.get("/systems/{system_id}/members/{member_id}", summary="One visible member")
    async def get_member(system_id: str, member_id: str, who: Viewer = Depends(viewer),
                         store: Store = Depends(db)) -> dict[str, Any]:
        return await run(member_payload, store, system_id, member_id, who)

    @router.get("/systems/{system_id}/groups", summary="A system's visible groups")
    async def list_groups(system_id: str, who: Viewer = Depends(viewer),
                          store: Store = Depends(db)) -> dict[str, Any]:
        return await run(groups_payload, store, system_id, who)

    @router.get("/systems/{system_id}/groups/{group_id}", summary="One visible group")
    async def get_group(system_id: str, group_id: str, who: Viewer = Depends(viewer),
                        store: Store = Depends(db)) -> dict[str, Any]:
        return await run(group_payload, store, system_id, group_id, who)

    @router.get("/systems/{system_id}/fronters", summary="Who is fronting right now")
    async def fronters(system_id: str, who: Viewer = Depends(viewer),
                       store: Store = Depends(db)) -> dict[str, Any]:
        return await run(fronters_payload, store, system_id, who)

    return router
