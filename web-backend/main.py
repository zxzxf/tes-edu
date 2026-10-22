"""Web 管理后台服务（B 端运营中枢）。

提供教研内容管理、学情看板、错题本、自动组卷、教师批改、家长周报、合规审核等接口。
所有路由挂在 /api/admin 下（由 Go 网关剥离前缀后转发到此服务）。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    knowledge,
    analytics,
    wrong_questions,
    exam,
    grading,
    reports,
    compliance,
    questions,
    ingest,
)

app = FastAPI(title="Tes Edu Web Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# 统一前缀 /api/admin（网关已剥离此前缀再转发，这里重新挂上以便本地直连也一致）
app.include_router(auth.router, prefix="/api/admin")
app.include_router(knowledge.router, prefix="/api/admin")
app.include_router(analytics.router, prefix="/api/admin")
app.include_router(wrong_questions.router, prefix="/api/admin")
app.include_router(exam.router, prefix="/api/admin")
app.include_router(grading.router, prefix="/api/admin")
app.include_router(reports.router, prefix="/api/admin")
app.include_router(compliance.router, prefix="/api/admin")
app.include_router(questions.router, prefix="/api/admin")
app.include_router(ingest.router, prefix="/api/admin")


@app.on_event("startup")
async def on_startup():
    # course_kb 建表幂等；DB 未就绪时不阻断服务启动
    try:
        from app.services import course_kb as svc

        await svc.init_course_kb()
    except Exception as e:  # noqa: BLE001
        print("[startup] course_kb 初始化跳过：", e)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
