"""Public health endpoints."""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """Report that the API process is alive."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    """Report whether the configured PostgreSQL connection is usable."""
    try:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("select 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ready"}
