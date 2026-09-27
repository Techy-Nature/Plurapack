"""FastAPI dashboard server sharing Plurapack's existing SQLite Store."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from .storage import Form, Front, Member, Store, System
from .login import LOGIN_START_WINDOW, LoginError, LoginService, LoginStartLimiter
from .web_auth import (COOKIE_NAME, SESSION_LIFETIME, WebUser, cookie_secure,
                       create_session_cookie, require_authenticated_user)
from .web_models import FormCreate, FormPatch, FrontUpdate, MemberCreate, MemberPatch, SystemPatch

ROOT = Path(__file__).resolve().parent.parent


class LoginCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser_secret: str


def form_json(form: Form) -> dict[str, Any]:
    return {"id": form.id, "memberId": form.member_id, "displayName": form.display_name,
            "picture": form.avatar, "soma": form.soma, "pronouns": form.pronouns,
            "prefix": form.prefix, "suffix": form.suffix, "banner": form.banner}


def member_json(store: Store, member: Member, front: Front | None = None) -> dict[str, Any]:
    return {"id": member.id, "systemId": member.system_id, "name": member.name,
            "alias": member.alias, "pronouns": member.pronouns, "color": member.color or "#7765A8",
            "avatar": member.avatar, "description": member.description,
            "prefix": member.prefix, "suffix": member.suffix, "proxy": f"{member.prefix}{member.suffix}",
            "banner": member.banner,
            "defaultFormId": member.default_form_id,
            "fronting": bool(front and front.member.id == member.id),
            "forms": [form_json(form) for form in store.forms_for_member(member.id)],
            "voice": {"configured": member.voice_reference is not None,
                      "settings": json.loads(member.voice_settings), "playback": member.playback,
                      "supportedPlayback": ["off", "send"],
                      "speechFormatting": bool(member.speech_formatting),
                      "strikethroughSpeech": member.strikethrough_speech}}


def system_json(store: Store, account_id: str, system: System) -> dict[str, Any]:
    front, autoproxy = store.current_front(account_id), store.autoproxy(account_id)
    return {"id": system.id, "displayName": system.display_name, "name": system.display_name,
            "description": system.description, "logo": system.logo, "banner": system.banner, "tag": system.system_tag,
            "showSystemTag": bool(system.show_system_tag),
            "members": [member_json(store, member, front) for member in store.members_for_system(system.id)],
            "front": front_json(front),
            "autoproxy": {"memberId": autoproxy.member.id if autoproxy.member else None,
                          "autofront": autoproxy.autofront}}


def front_json(front: Front | None) -> dict[str, Any]:
    return {"memberId": front.member.id if front else None,
            "formId": front.form.id if front and front.form else None}


def create_app(store: Store | None = None, static_root: Path | None = ROOT) -> FastAPI:
    app = FastAPI(title="Plurapack Web API", version="1")
    app.state.store = store or Store(os.getenv("PLURAPACK_DATABASE", "plurapack.sqlite3"))
    app.state.login_service = LoginService(app.state.store)
    app.state.login_start_limiter = LoginStartLimiter(
        int(os.getenv("PLURAPACK_LOGIN_START_LIMIT", "10"))
    )

    @app.exception_handler(HTTPException)
    async def api_http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"message": str(exc.detail)}, status_code=exc.status_code,
                                headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"] if part != "body")
        message = f"{location}: {first['msg']}" if location else str(first["msg"])
        return JSONResponse({"message": message}, status_code=422)

    def db(request: Request) -> Store:
        return request.app.state.store

    async def run(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    async def authorized(system_id: str, user: WebUser, store: Store) -> System:
        system = await run(store.system_info, system_id)
        if system is None:
            raise HTTPException(404, "Resource not found")
        if not await run(store.account_has_system, user.id, system_id):
            raise HTTPException(403, "You do not have access to this system")
        return system

    async def owned_member(system_id: str, member_id: str, user: WebUser, store: Store) -> Member:
        await authorized(system_id, user, store)
        member = await run(store.member_selected, user.id, member_id)
        if member is None or member.system_id != system_id:
            raise HTTPException(404, "Resource not found")
        return member

    async def owned_form(system_id: str, member_id: str, form_id: str,
                         user: WebUser, store: Store) -> Form:
        await owned_member(system_id, member_id, user, store)
        selected = await run(store.form_selected, user.id, form_id)
        if selected is None or selected[0].member_id != member_id:
            raise HTTPException(404, "Resource not found")
        return selected[0]

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login/start")
    async def start_login(request: Request) -> dict[str, Any]:
        # Deliberately use the directly connected peer. Forwarded headers are
        # attacker-controlled unless proxy trust is configured outside the app.
        client = request.client.host if request.client else "unknown"
        if not request.app.state.login_start_limiter.allow(client):
            raise HTTPException(429, "Too many login attempts; try again shortly",
                                headers={"Retry-After": str(LOGIN_START_WINDOW)})
        attempt = await asyncio.to_thread(request.app.state.login_service.start)
        return {"attemptId": attempt.id, "code": attempt.code,
                "browserSecret": attempt.browser_secret, "expiresIn": attempt.expires_in,
                "commandPrefix": os.getenv("PLURAPACK_PREFIX", "p;")}

    @app.get("/api/auth/login/{attempt_id}")
    async def login_status(attempt_id: str, request: Request) -> dict[str, str]:
        try:
            current = await asyncio.to_thread(request.app.state.login_service.status, attempt_id)
        except LoginError as error:
            raise HTTPException(404, str(error)) from error
        return {"status": current}

    @app.post("/api/auth/login/{attempt_id}/complete")
    async def complete_login(attempt_id: str, body: LoginCompletion, request: Request) -> Response:
        # Check session configuration before atomically consuming the attempt.
        try:
            create_session_cookie(WebUser("configuration-check", "configuration-check"), 1)
        except RuntimeError as error:
            raise HTTPException(503, "Dashboard authentication is not configured") from error
        try:
            user = await asyncio.to_thread(
                request.app.state.login_service.complete, attempt_id, body.browser_secret)
        except LoginError as error:
            raise HTTPException(400, str(error)) from error
        response = JSONResponse({"status": "authenticated"})
        response.set_cookie(COOKIE_NAME, create_session_cookie(user), max_age=SESSION_LIFETIME,
                            httponly=True, secure=cookie_secure(), samesite="lax", path="/")
        return response

    @app.post("/api/auth/logout", status_code=204)
    async def logout() -> Response:
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                               secure=cookie_secure(), samesite="lax")
        return response

    @app.get("/api/account")
    async def account(user: WebUser = Depends(require_authenticated_user),
                      store: Store = Depends(db)) -> dict[str, Any]:
        systems = await run(store.systems_for_account, user.id)
        summaries = [{"id": item.id, "name": item.display_name, "tag": item.system_tag,
                      "memberCount": len(await run(store.members_for_system, item.id))} for item in systems]
        return {"authenticated": True, "id": user.id, "username": user.username,
                "displayName": user.username, "avatar": user.avatar,
                "systemId": summaries[0]["id"] if summaries else None,
                "user": {"id": user.id, "username": user.username, "avatar": user.avatar},
                "systems": summaries}

    @app.get("/api/systems/{system_id}")
    async def get_system(system_id: str, user: WebUser = Depends(require_authenticated_user),
                         store: Store = Depends(db)) -> dict[str, Any]:
        return await run(system_json, store, user.id, await authorized(system_id, user, store))

    @app.patch("/api/systems/{system_id}")
    async def patch_system(system_id: str, body: SystemPatch,
                           user: WebUser = Depends(require_authenticated_user),
                           store: Store = Depends(db)) -> dict[str, Any]:
        await authorized(system_id, user, store)
        changes = body.model_dump(exclude_unset=True)
        changes = {key: str(value) if key in {"logo", "banner"} and value else int(value) if key == "show_system_tag" else value
                   for key, value in changes.items()}
        updated = await run(store.update_system, user.id, system_id, **changes)
        return await run(system_json, store, user.id, updated)

    @app.delete("/api/systems/{system_id}", status_code=204)
    async def delete_system(system_id: str, user: WebUser = Depends(require_authenticated_user),
                            store: Store = Depends(db)) -> Response:
        await authorized(system_id, user, store)
        await run(store.delete_system, user.id, system_id)
        return Response(status_code=204)

    @app.get("/api/systems/{system_id}/members")
    async def list_members(system_id: str, user: WebUser = Depends(require_authenticated_user),
                           store: Store = Depends(db)) -> list[dict[str, Any]]:
        await authorized(system_id, user, store)
        front = await run(store.current_front, user.id)
        return [await run(member_json, store, member, front)
                for member in await run(store.members_for_system, system_id)]

    @app.post("/api/systems/{system_id}/members", status_code=201)
    async def create_member(system_id: str, body: MemberCreate,
                            user: WebUser = Depends(require_authenticated_user),
                            store: Store = Depends(db)) -> dict[str, Any]:
        await authorized(system_id, user, store)
        # Like the bot's member command, the API requires the caller to choose
        # the proxy rather than inventing a dashboard-only default.
        prefix = body.prefix if body.prefix is not None else body.proxy
        assert prefix is not None  # Enforced by MemberCreate.explicit_proxy_required.
        member = await run(store.add_member, user.id, body.name, prefix, body.suffix,
                           body.description or "", alias=body.alias, pronouns=body.pronouns,
                           color=body.color, avatar=str(body.avatar) if body.avatar else None)
        return await run(member_json, store, member, None)

    @app.get("/api/systems/{system_id}/members/{member_id}")
    async def get_member(system_id: str, member_id: str,
                         user: WebUser = Depends(require_authenticated_user),
                         store: Store = Depends(db)) -> dict[str, Any]:
        member = await owned_member(system_id, member_id, user, store)
        return await run(member_json, store, member, await run(store.current_front, user.id))

    @app.patch("/api/systems/{system_id}/members/{member_id}")
    async def patch_member(system_id: str, member_id: str, body: MemberPatch,
                           user: WebUser = Depends(require_authenticated_user),
                           store: Store = Depends(db)) -> dict[str, Any]:
        member = await owned_member(system_id, member_id, user, store)
        changes = body.model_dump(exclude_unset=True)
        if "default_form_id" in changes:
            member = await run(store.configure_default_form, user.id, member.id, changes.pop("default_form_id"))
        playback = changes.pop("playback", None)
        voice_settings = changes.pop("voice_settings", None)
        if playback is not None or voice_settings is not None:
            member = await run(
                store.configure_voice, user.id, member.id, member.voice_reference,
                voice_settings if voice_settings is not None else member.voice_settings,
                playback if playback is not None else member.playback,
            )
        changes = {key: str(value) if key in {"avatar", "banner"} and value else value for key, value in changes.items()}
        if changes:
            member = await run(store.update_member, user.id, member.id, **changes)
        return await run(member_json, store, member, await run(store.current_front, user.id))

    @app.delete("/api/systems/{system_id}/members/{member_id}", status_code=204)
    async def remove_member(system_id: str, member_id: str,
                            user: WebUser = Depends(require_authenticated_user),
                            store: Store = Depends(db)) -> Response:
        await owned_member(system_id, member_id, user, store)
        await run(store.delete_member, user.id, member_id)
        return Response(status_code=204)

    @app.get("/api/systems/{system_id}/members/{member_id}/forms")
    async def list_forms(system_id: str, member_id: str,
                         user: WebUser = Depends(require_authenticated_user),
                         store: Store = Depends(db)) -> list[dict[str, Any]]:
        await owned_member(system_id, member_id, user, store)
        return [form_json(form) for form in await run(store.forms_for_member, member_id)]

    @app.post("/api/systems/{system_id}/members/{member_id}/forms", status_code=201)
    async def create_form(system_id: str, member_id: str, body: FormCreate,
                          user: WebUser = Depends(require_authenticated_user),
                          store: Store = Depends(db)) -> dict[str, Any]:
        await owned_member(system_id, member_id, user, store)
        form = await run(store.create_form, user.id, member_id, body.display_name,
                         str(body.picture) if body.picture else None, body.soma, body.pronouns,
                         body.prefix, body.suffix, str(body.banner) if body.banner else None)
        return form_json(form)

    @app.get("/api/systems/{system_id}/members/{member_id}/forms/{form_id}")
    async def get_form(system_id: str, member_id: str, form_id: str,
                       user: WebUser = Depends(require_authenticated_user),
                       store: Store = Depends(db)) -> dict[str, Any]:
        return form_json(await owned_form(system_id, member_id, form_id, user, store))

    @app.patch("/api/systems/{system_id}/members/{member_id}/forms/{form_id}")
    async def patch_form(system_id: str, member_id: str, form_id: str, body: FormPatch,
                         user: WebUser = Depends(require_authenticated_user),
                         store: Store = Depends(db)) -> dict[str, Any]:
        await owned_form(system_id, member_id, form_id, user, store)
        changes = body.model_dump(exclude_unset=True)
        if "picture" in changes:
            changes["avatar"] = str(changes.pop("picture")) if changes["picture"] else None
        if "banner" in changes and changes["banner"]:
            changes["banner"] = str(changes["banner"])
        return form_json(await run(store.update_form, user.id, form_id, **changes))

    @app.delete("/api/systems/{system_id}/members/{member_id}/forms/{form_id}", status_code=204)
    async def remove_form(system_id: str, member_id: str, form_id: str,
                          user: WebUser = Depends(require_authenticated_user),
                          store: Store = Depends(db)) -> Response:
        await owned_form(system_id, member_id, form_id, user, store)
        await run(store.delete_form, user.id, form_id)
        return Response(status_code=204)

    @app.get("/api/systems/{system_id}/front")
    async def get_front(system_id: str, user: WebUser = Depends(require_authenticated_user),
                        store: Store = Depends(db)) -> dict[str, Any]:
        await authorized(system_id, user, store)
        return front_json(await run(store.current_front, user.id))

    @app.put("/api/systems/{system_id}/front")
    async def put_front(system_id: str, body: FrontUpdate,
                        user: WebUser = Depends(require_authenticated_user),
                        store: Store = Depends(db)) -> dict[str, Any]:
        await authorized(system_id, user, store)
        if body.member_id is None:
            if body.form_id is not None:
                raise HTTPException(422, "formId requires memberId")
            await run(store.clear_front, user.id)
            return front_json(None)
        await owned_member(system_id, body.member_id, user, store)
        selector = body.member_id
        if body.form_id:
            await owned_form(system_id, body.member_id, body.form_id, user, store)
            selector = body.form_id
        return front_json(await run(store.switch_front, user.id, selector))

    if static_root:
        @app.get("/login", include_in_schema=False)
        async def login_page() -> FileResponse:
            return FileResponse(static_root / "login.html")
        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(static_root / "index.html")

        @app.get("/app.js", include_in_schema=False)
        async def javascript() -> FileResponse:
            return FileResponse(static_root / "app.js", media_type="text/javascript")

        @app.get("/styles.css", include_in_schema=False)
        async def stylesheet() -> FileResponse:
            return FileResponse(static_root / "styles.css", media_type="text/css")

    return app


app = create_app()


def main() -> None:
    uvicorn.run("plurapack.web:app", host=os.getenv("PLURAPACK_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("PLURAPACK_WEB_PORT", "8000")))


if __name__ == "__main__":
    main()
