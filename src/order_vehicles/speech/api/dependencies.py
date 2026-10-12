import hmac

from fastapi import Header, HTTPException, Request


def service(request: Request, name: str):
    return request.app.state.services[name]


async def require_management_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    expected = request.app.state.settings.management_token.get_secret_value()
    if not expected:
        return
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:]
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid management token")
