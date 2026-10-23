"""HTTP adapter for the platform stand-in (D14). Development only, until login exists.

Who is calling comes from an `X-Dev-User: <tenant>/<user>` header: the platform's stand-in
for login, as DEV_USER is the MCP server's. It works only with APP_ENV=dev, and the app
refuses to start otherwise. Login replaces it with tokens issued for the *platform's*
audience, never MCP tokens.

Browser-facing protections, since this app renders text written by users:
- the page inserts user text with textContent only (no HTML parsing), and a strict
  Content-Security-Policy blocks inline and third-party scripts;
- only 127.0.0.1/localhost Host headers are accepted (DNS rebinding);
- no CORS is enabled, so a foreign site can't send the custom header (CSRF).
"""

import os
from pathlib import Path

import uvicorn
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from frontline_training_mcp import service
from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import (
    Identity,
    dev_directory,
    lookup_identity,
    parse_dev_user,
)

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = ["127.0.0.1", "localhost"]

ERROR_STATUS = {
    service.InvalidRequest: 400,
    service.Forbidden: 403,
    service.NotFound: 404,
    service.Conflict: 409,
}

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; "
        b"base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


class Unauthorized(Exception):
    pass


class SecurityHeaders:
    """Adds the browser protections above to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)


def dev_mode() -> bool:
    return os.environ.get("APP_ENV") == "dev"


def create_app(db: Database, allowed_hosts: list[str] = LOCAL_HOSTS) -> Starlette:
    def caller(request: Request) -> Identity:
        if not dev_mode():
            raise Unauthorized("No login exists yet: the stand-in only runs in dev mode.")
        try:
            tenant_id, user_id = parse_dev_user(request.headers.get("x-dev-user", ""))
            return lookup_identity(db, tenant_id, user_id, audience="platform")
        except (RuntimeError, LookupError) as exc:
            raise Unauthorized("Choose who to view as.") from exc

    # Endpoints are plain functions: Starlette runs them in a thread pool, so blocking
    # database calls never stall the event loop (the same reasoning as D15).

    def respond(result: BaseModel) -> JSONResponse:
        return JSONResponse(result.model_dump(mode="json"))

    def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html")

    def users(request: Request) -> Response:
        if not dev_mode():
            return JSONResponse({"error": "Not found."}, status_code=404)
        return JSONResponse({"users": dev_directory(db)})

    def me(request: Request) -> Response:
        return respond(service.profile(db, caller(request)))

    def assignments(request: Request) -> Response:
        return respond(service.my_assignments(db, caller(request), "all", 50, None))

    def announcements(request: Request) -> Response:
        return respond(service.list_announcements(db, caller(request), 20, None))

    def team(request: Request) -> Response:
        return respond(service.team_progress(db, caller(request), None, 50, None))

    def drafts(request: Request) -> Response:
        return respond(service.my_drafts(db, caller(request)))

    def publish(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        return respond(service.publish_draft(db, caller(request), draft_id))

    async def unauthorized(request: Request, exc: Exception) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=401)

    async def refused(request: Request, exc: Exception) -> Response:
        status = next(code for kind, code in ERROR_STATUS.items() if isinstance(exc, kind))
        return JSONResponse({"error": str(exc)}, status_code=status)

    return Starlette(
        routes=[
            Route("/", index),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
            Route("/api/dev/users", users),
            Route("/api/me", me),
            Route("/api/me/assignments", assignments),
            Route("/api/me/announcements", announcements),
            Route("/api/me/team", team),
            Route("/api/me/drafts", drafts),
            Route("/api/drafts/{draft_id:int}/publish", publish, methods=["POST"]),
        ],
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
            Middleware(SecurityHeaders),
        ],
        exception_handlers={Unauthorized: unauthorized, service.ServiceError: refused},
    )


def main() -> None:
    if not dev_mode():
        raise SystemExit("The platform stand-in has no login yet: it only runs with APP_ENV=dev.")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set.")
    port = int(os.environ.get("STANDIN_PORT", "8081"))
    uvicorn.run(create_app(Database(database_url)), host="127.0.0.1", port=port)
