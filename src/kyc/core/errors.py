"""API failures keep technical unavailability separate from identity decisions."""

from fastapi.responses import JSONResponse


def error_response(status_code: int, reason_code: str, detail: str, *, headers: dict | None = None,
                   **extra) -> JSONResponse:
    body = {"detail": detail, "reason_code": reason_code}
    if status_code >= 500:
        body.update(result="TECHNICAL_ERROR", retry_allowed=True)
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body, headers=headers)
