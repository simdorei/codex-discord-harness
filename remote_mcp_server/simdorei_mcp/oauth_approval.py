from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remote_mcp_server.simdorei_mcp.oauth_provider import (
    ApprovalDeniedError,
    ApprovalNotFoundError,
    SingleUserOAuthProvider,
)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class ApprovalSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str = Field(min_length=20, max_length=200)
    owner_token: str = Field(min_length=1, max_length=500)


def create_approval_router(provider: SingleUserOAuthProvider) -> APIRouter:
    router = APIRouter()

    @router.get("/oauth/approve", response_class=HTMLResponse)
    async def approval_page(request_id: str) -> HTMLResponse:
        if not await provider.pending_exists(request_id):
            return _page("This authorization request expired.", status_code=400)
        return _page(_approval_form(request_id))

    @router.post("/oauth/approve")
    async def approve(request: Request) -> Response:
        try:
            submission = ApprovalSubmission.model_validate(dict(await request.form()))
        except ValidationError:
            return _page("Invalid authorization request.", status_code=400)
        try:
            redirect_url = await provider.approve(
                submission.request_id,
                submission.owner_token,
            )
        except ApprovalNotFoundError:
            return _page("This authorization request expired.", status_code=400)
        except ApprovalDeniedError:
            return _page(
                _approval_form(
                    submission.request_id,
                    message="The owner token did not match.",
                ),
                status_code=401,
            )
        return RedirectResponse(
            redirect_url,
            status_code=302,
            headers=SECURITY_HEADERS,
        )

    return router


def _approval_form(request_id: str, message: str = "") -> str:
    safe_request_id = escape(request_id, quote=True)
    safe_message = escape(message)
    feedback = f'<p class="error">{safe_message}</p>' if safe_message else ""
    return f"""
    <main>
      <h1>Connect ChatGPT to your local project</h1>
      <p>Enter the private owner token stored for this MCP server.</p>
      {feedback}
      <form method="post" action="/oauth/approve">
        <input type="hidden" name="request_id" value="{safe_request_id}">
        <label>Owner token
          <input type="password" name="owner_token" required autofocus>
        </label>
        <button type="submit">Connect</button>
      </form>
    </main>
    """


def _page(content: str, status_code: int = 200) -> HTMLResponse:
    html = f"""<!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport"
    content="width=device-width,initial-scale=1"><title>Simdorei MCP OAuth</title>
    <style>
      body {{ font: 16px system-ui; background:#111827; color:#f9fafb; margin:0; }}
      main {{ max-width:32rem; margin:12vh auto; padding:2rem; background:#1f2937;
              border-radius:1rem; }}
      label,input,button {{ display:block; width:100%; box-sizing:border-box; }}
      input,button {{ margin-top:.5rem; padding:.8rem; border-radius:.5rem; }}
      button {{ margin-top:1rem; cursor:pointer; }} .error {{ color:#fca5a5; }}
    </style></head><body>{content}</body></html>"""
    return HTMLResponse(html, status_code=status_code, headers=SECURITY_HEADERS)
