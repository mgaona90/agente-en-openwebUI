import time

from fastapi import APIRouter

router = APIRouter(tags=["models"])

_MODEL_ID = "e2e-test-agent6"


@router.get("/v1/models")
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": _MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "scanntech",
            }
        ],
    }
