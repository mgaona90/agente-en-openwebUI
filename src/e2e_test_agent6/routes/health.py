from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
@router.get("/readyz")
@router.get("/health")
async def healthz() -> dict:
    return {"status": "ok"}
