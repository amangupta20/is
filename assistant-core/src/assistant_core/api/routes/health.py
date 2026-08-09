"""Public health endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """Report that the API process is alive."""
    return {"status": "ok"}
