from fastapi import APIRouter

router = APIRouter(tags=["health"])
routers = [router]


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
