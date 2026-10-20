from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.solve import router as solve_router

app = FastAPI(title="Tes Edu Agent Backend", version="0.1.0")

# P0：网关已做鉴权/限流；此处 CORS 仅便于本地联调，生产可收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(solve_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
