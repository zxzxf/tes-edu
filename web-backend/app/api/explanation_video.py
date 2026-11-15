"""④·数字人讲题短视频 API（ROADMAP #28）。

学生端（网关 /api/student/ 透传学生身份）：
- GET  /explanation-video/by-question?question=...   查当前租户该题是否有可看视频
- GET  /explanation-video/list                      本租户已出片视频列表（发现入口）
- POST /explanation-video/ensure                    按错题题干自助生成 / 复用专属讲解视频
- GET  /explanation-video/{id}/stream               带鉴权取流（App 下载到本地再播）
- GET  /explanation-videos/{id}.mp4                 兼容历史直链（同样复校租户）

教师 / 后台（网关 /api/admin/，需角色）：
- POST /explanation-video/generate   触发 / 复用生成任务（幂等）
- POST /explanation-video/regen      重新生成（done / failed 也强制重跑）
- GET  /explanation-video/config     可用形象 / 音色列表 + 总开关
"""
from __future__ import annotations

import difflib
import logging
import os
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.db import get_db
from app.core.rbac import Role
from app.core.config import settings
from app.core.security import business_tenant_id, get_current_user, require_role
from app.models.explanation_video import ExplanationVideo
from app.services import object_storage
from app.services.video_generator import content_hash, run_generation

student_router = APIRouter(tags=["explanation-video"])
admin_router = APIRouter(tags=["explanation-video-admin"])

logger = logging.getLogger("explanation_video")

# IVH 最长轮询 30 分钟，超过这个时长的 pending/processing 只可能是「容器重启把
# BackgroundTasks 弄丢了」的僵尸行，允许重新排队（不会与仍在渲染的任务打架）。
_STALE_TASK_MINUTES = 35


class GenerateIn(BaseModel):
    question: str = Field(..., min_length=1)
    knowledge_point: Optional[str] = None
    # 讲题台词稿；不传则退化为题目文本本身
    script: Optional[str] = None
    avatar_id: Optional[str] = "default"
    voice_id: Optional[str] = "default"


class EnsureIn(BaseModel):
    """学生端按错题自助生成：讲稿由 App 用「题干 + 参考答案 + 我的作答」拼好。

    讲稿上限与 IVH InputSsml 的 300 字对齐，避免超长台词被截成半句；题干本身
    不设上限（解答题题干可能很长），它只作为 content_hash 的匹配键。
    """

    question: str = Field(..., min_length=1)
    script: Optional[str] = Field(None, max_length=300)
    knowledge_point: Optional[str] = Field(None, max_length=100)


def _require_enabled() -> None:
    if not settings.digital_human_enabled:
        raise HTTPException(status_code=403, detail="数字人管线未启用（digital_human_enabled=false）")


def _is_stale_task(row: ExplanationVideo) -> bool:
    """判断 pending/processing 是否已经没人收尾（容器重启丢掉后台任务）。

    SQLite 单测里读出来的是 naive datetime，PG 是 aware，统一补 UTC 再比。
    """
    ts = row.updated_at
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() > _STALE_TASK_MINUTES * 60


async def _ensure_video_row(
    db,
    tid: str,
    body: GenerateIn,
    background: BackgroundTasks,
    max_inflight: Optional[int] = None,
) -> ExplanationVideo:
    """按 (tenant, content_hash(question)) 幂等建/复用生成任务。

    教师端 ``generate`` 与学生端 ``ensure`` 共用，避免两处口径漂移；
    ``max_inflight`` 只给学生端用，限制同租户在途任务数，防止误触刷爆 IVH 配额。
    """
    h = content_hash(body.question)
    # 读写共用同一套匹配口径：App 用 by-question 判定「这题有视频」，写侧就必须
    # 复用同一条，否则 App 明明能播、ensure 却又排一条新任务，白等几分钟还烧配额。
    row, match = await _find_video_row(db, tid, body.question, allow_fuzzy=True)
    if row is not None and match != "exact":
        logger.info(
            "video row %s 以 %s 命中同题成片，直接复用不重复生成", row.id, match
        )

    if row is not None and row.status in ("pending", "processing", "done"):
        if row.status != "done" and _is_stale_task(row):
            logger.warning(
                "video row %s 卡在 %s 超过 %s 分钟，重新排队",
                row.id, row.status, _STALE_TASK_MINUTES,
            )
            row.status = "pending"
            row.error = None
            await db.commit()
            background.add_task(run_generation, row.id)
        return row  # 幂等复用：不重复起后台任务

    if row is None:
        if max_inflight is not None:
            inflight = (
                await db.execute(
                    select(func.count())
                    .select_from(ExplanationVideo)
                    .where(
                        ExplanationVideo.tenant_id == tid,
                        ExplanationVideo.status.in_(("pending", "processing")),
                    )
                )
            ).scalar_one()
            if inflight >= max_inflight:
                raise HTTPException(
                    status_code=429, detail="讲解视频生成队列繁忙，请稍后再试"
                )
        row = ExplanationVideo(
            tenant_id=tid,
            content_hash=h,
            question_text=body.question,
            knowledge_point=body.knowledge_point,
            script=body.script or body.question,
            avatar_id=body.avatar_id,
            voice_id=body.voice_id,
            status="pending",
        )
        db.add(row)
    else:  # failed → 复位重试
        row.status = "pending"
        row.error = None
        row.script = body.script or row.script or body.question
    await db.commit()
    await db.refresh(row)
    background.add_task(run_generation, row.id)
    return row


def _qkey(text: str) -> str:
    """题干归一化键：NFKC + 去空白/标点 + 小写。

    ``content_hash`` 是对原始题干逐字取的 sha256，学生在 App 里手打、从错题本带过来的
    题干只要多一个空格或全角标点就查不到同一条视频。这里只用于「兜底查找」，
    不参与落库，避免改动历史数据的 hash 口径。
    """
    s = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(
        ch for ch in s
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


async def _find_video_row(db, tid: str, question: str, *, allow_fuzzy: bool = True):
    """按题干定位本租户的视频任务，返回 ``(row, match)``。

    ``match`` ∈ exact / normalized / fuzzy / none：

    - ``exact``：``content_hash`` 逐字命中（任意状态，含 failed，便于复位重跑）；
    - ``normalized``：题干只差空白/全角标点/大小写，在**已出片**视频里命中；
    - ``fuzzy``：相似度 ≥0.90，同题的细微改写；门槛收紧且只在已出片视频里比较。

    这是「同一道题已经有视频就不要再生成一次」的唯一口径：``by-question``（学生查）
    与 ``_ensure_video_row``（学生/教师写）共用，避免两处匹配规则漂移导致重复出片。
    ``allow_fuzzy=False`` 保留给将来需要「只认逐字同题」的写场景。
    """
    h = content_hash(question)
    row = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.content_hash == h,
            )
            .order_by(ExplanationVideo.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is not None:
        return row, "exact"
    return await _fallback_video_row(db, tid, question, allow_fuzzy=allow_fuzzy)


async def _fallback_video_row(db, tid: str, question: str, *, allow_fuzzy: bool):
    """精确 hash 未命中时，在本租户已出片视频里找「同一题的另一种排版」。"""
    candidates = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.status == "done",
                ExplanationVideo.video_url.isnot(None),
            )
            .order_by(ExplanationVideo.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    key = _qkey(question)
    best, best_ratio = None, 0.0
    for cand in candidates:
        cand_key = _qkey(cand.question_text or "")
        if cand_key and cand_key == key:
            best, best_ratio = cand, 1.0
            break
        ratio = difflib.SequenceMatcher(None, key, cand_key).ratio()
        if ratio > best_ratio:
            best, best_ratio = cand, ratio
    if best is None or len(key) < 6:
        return None, "none"
    if best_ratio == 1.0:
        return best, "normalized"
    if allow_fuzzy and best_ratio >= 0.90:
        return best, "fuzzy"
    return None, "none"


def _row_out(row: ExplanationVideo) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "status": row.status,
        "video_url": row.video_url,
        "duration_sec": row.duration_sec,
        "error": row.error,
        "content_hash": row.content_hash,
        "question_text": row.question_text,
        "knowledge_point": row.knowledge_point,
        "avatar_id": row.avatar_id,
        "voice_id": row.voice_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@student_router.get("/explanation-video/by-question")
async def by_question(
    question: str = Query(..., min_length=1),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = business_tenant_id(user)
    # 与写侧 ensure 共用同一套匹配口径：精确 hash → 归一化相同 → 只读才放宽到 fuzzy
    row, match = await _find_video_row(db, tid, question, allow_fuzzy=True)
    if row is not None:
        out = _row_out(row)
        out["match"] = match
        return out

    return {
        "id": None,
        "status": "none",
        "video_url": None,
        "duration_sec": None,
        "error": None,
        "content_hash": content_hash(question),
        "match": "none",
    }


@student_router.get("/explanation-video/list")
async def list_student_videos(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """学生端「已有讲解视频」列表。

    之前学生只能靠 ``by-question`` 用**逐字相同**的题干碰运气，教师端批量生成的
    视频对学生完全不可见（App 里就表现为「看视频讲解不能用」）。这里补齐发现入口：
    只列本租户、已出片、有 URL 的任务；取流仍走 ``/{id}/stream`` 的租户复校。
    """
    tid = business_tenant_id(user)
    q = select(ExplanationVideo).where(
        ExplanationVideo.tenant_id == tid,
        ExplanationVideo.status == "done",
        ExplanationVideo.video_url.isnot(None),
    )
    total = (
        await db.execute(select(func.count()).select_from(q.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            q.order_by(ExplanationVideo.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    keys = ("id", "question_text", "knowledge_point", "duration_sec", "created_at")
    return {"total": total, "items": [{k: getattr(r, k) for k in keys} for r in rows]}


@student_router.post("/explanation-video/ensure")
async def ensure_video(
    body: EnsureIn,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """错题本「视频讲解」：按这道题的题干自助生成 / 复用一条专属视频。

    教师端批量生成的视频题干与学生错题永远对不上，学生端点开就是「暂无视频」。
    这里把生成能力下沉到学生侧（仍是同一套 ``run_generation`` / IVH 管线），
    按 ``business_tenant_id`` + ``content_hash(question)`` 幂等：
    已有 done 直接返回 App 去取流，pending/processing 让 App 轮询，failed 复位重跑。
    """
    _require_enabled()
    tid = business_tenant_id(user)
    row = await _ensure_video_row(
        db,
        tid,
        GenerateIn(
            question=body.question,
            script=body.script,
            knowledge_point=body.knowledge_point,
        ),
        background,
        max_inflight=5,
    )
    return _row_out(row)


async def _load_video_bytes(video_id: str) -> tuple[Optional[bytes], Optional[str]]:
    """取视频字节：MinIO 优先，未部署 MinIO / 历史本地产物回退本地目录。

    ③·对象存储接入后外部 URL 与网关均不变，取流统一走这里，避免三处实现漂移。
    """
    object_name = f"explanation_videos/{video_id}.mp4"
    data, ctype = await object_storage.fetch_video(object_name)
    if data is None:
        local = os.path.join(settings.explanation_video_dir, f"{video_id}.mp4")
        if os.path.exists(local):
            with open(local, "rb") as f:
                data = f.read()
            ctype = "video/mp4"
    return data, ctype


async def _student_video_or_404(db, video_id: str, user: dict) -> ExplanationVideo:
    """学生取流前的行级复校：必须存在、必须是本租户、必须已出片。

    历史 ``serve_video`` 只靠网关 ``jwt_c`` 挡一下，登录后手工拼任意 id 就能跨租户
    取流。这里按 ``by_question`` 同一口径（``business_tenant_id``）复校；
    越权与不存在统一返 404，避免把别校视频的存在性泄露出去。
    """
    row = (
        await db.execute(select(ExplanationVideo).where(ExplanationVideo.id == video_id))
    ).scalar_one_or_none()
    if (
        row is None
        or row.tenant_id != business_tenant_id(user)
        or row.status != "done"
    ):
        raise HTTPException(status_code=404, detail="video not found")
    return row


def _video_response(data: Optional[bytes], ctype: Optional[str]) -> Response:
    if data is None:
        raise HTTPException(status_code=404, detail="video not found")
    return Response(
        content=data,
        media_type=ctype or "video/mp4",
        headers={"Cache-Control": "private, no-store"},
    )


# ④·学生端 App「看视频讲解」：VideoView / MediaPlayer 无法给网络请求附加
# Authorization 头，直链必被网关 401；且 video_url 是相对路径，VideoView 也解析不了。
# 因此 App 用 Retrofit（AuthInterceptor 带 Bearer）把流下载到私有缓存再用本地路径播放。
@student_router.get("/explanation-video/{video_id}/stream")
async def stream_video_student(
    video_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _student_video_or_404(db, video_id, user)
    data, ctype = await _load_video_bytes(video_id)
    return _video_response(data, ctype)


# 兼容历史直链（/api/student/explanation-videos/{id}.mp4）：同样复校租户，
# 不再是无鉴权的裸取流。
@student_router.get("/explanation-videos/{video_id}.mp4")
async def serve_video(
    video_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _student_video_or_404(db, video_id, user)
    data, ctype = await _load_video_bytes(video_id)
    return _video_response(data, ctype)


# ------------------------- 教师 / 后台端 -------------------------

# ④·教师端「播放预览」：后台用老师自己的 admin+租户令牌取流（与 list 同源鉴权），
# 避免 <video src> 直链到 /api/student/ 时被网关因缺 Authorization 头拦截（401）。
# 流式返回 mp4，前端用 axios blob 拿到 objectURL 后内联播放。
@admin_router.get("/explanation-video/{video_id}/stream")
async def stream_video_admin(
    video_id: str,
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    is_admin = user.get("role") == Role.ADMIN.value
    # 行级租户隔离：teacher/region_admin 只能看自己业务租户的视频，admin 看全量
    row = (
        await db.execute(select(ExplanationVideo).where(ExplanationVideo.id == video_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="video not found")
    if not is_admin and row.tenant_id != business_tenant_id(user):
        raise HTTPException(status_code=403, detail="无权访问该视频")

    data, ctype = await _load_video_bytes(video_id)
    return _video_response(data, ctype)


@admin_router.get("/explanation-video/config")
async def config(user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER))):
    from app.core.config import settings

    impl = settings.digital_human_impl.strip().lower()
    is_ivh = impl in ("ivh", "digital_human", "saas")
    ivh_ready = bool(
        settings.ivh_appkey and settings.ivh_accesstoken and settings.ivh_ref_photo_url
    )
    return {
        "enabled": settings.digital_human_enabled,
        "impl": "ivh" if is_ivh else "lite",
        "true_digital_human": is_ivh and ivh_ready,
        "avatars": [{"id": "default", "name": "默认讲师（照片免训练形象）"}],
        "voices": [{"id": settings.ivh_timbre_key, "name": "平台内置音色"}],
        "note": (
            "真数字人走腾讯云 IVH SaaS 播报视频制作（137 零 GPU）；"
            "未配置或出片失败时自动回退 ffmpeg 字幕卡片，业务不中断"
            if is_ivh and ivh_ready
            else "当前为 Lite 字幕卡片（零 GPU）；配置 IVH_APPKEY / IVH_ACCESSTOKEN / "
                 "IVH_REF_PHOTO_URL 并设 DIGITAL_HUMAN_IMPL=ivh 后启用真数字人"
        ),
    }


@admin_router.get("/explanation-video/list")
async def list_videos(
    status: Optional[str] = Query(None, description="pending/processing/done/failed 过滤"),
    tenant_id: Optional[str] = Query(None, description="仅 ADMIN 可指定跨租户查询"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    """讲解视频生成任务列表（教师端任务管理后台用）。

    - ADMIN：不传 tenant_id 看全量，传了按租户过滤；
    - TEACHER / REGION_ADMIN：强制只看自己业务租户（与平台数据权限矩阵一致）。
    """
    is_admin = user.get("role") == Role.ADMIN.value
    q = select(ExplanationVideo)
    if is_admin:
        # ADMIN：显式传 tenant_id 才过滤，否则看全量（不被自身平台租户限死）
        if tenant_id:
            q = q.where(ExplanationVideo.tenant_id == tenant_id)
    else:
        # TEACHER / REGION_ADMIN：强制只看自己业务租户
        q = q.where(ExplanationVideo.tenant_id == business_tenant_id(user))
    if status:
        q = q.where(ExplanationVideo.status == status)

    total = (
        await db.execute(
            select(func.count()).select_from(q.subquery())
        )
    ).scalar_one()
    rows = (
        await db.execute(
            q.order_by(ExplanationVideo.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {"total": total, "items": [_row_out(r) for r in rows]}


@admin_router.post("/explanation-video/generate")
async def generate(
    body: GenerateIn,
    background: BackgroundTasks,
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    """教师端触发 / 复用生成任务；与学生端 ensure 共用 ``_ensure_video_row``。"""
    _require_enabled()
    tid = business_tenant_id(user)
    row = await _ensure_video_row(db, tid, body, background)
    return _row_out(row)


@admin_router.post("/explanation-video/regen")
async def regen(
    body: GenerateIn,
    background: BackgroundTasks,
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    _require_enabled()
    tid = business_tenant_id(user)
    h = content_hash(body.question)
    row = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.content_hash == h,
            )
            .order_by(ExplanationVideo.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        row = ExplanationVideo(
            tenant_id=tid,
            content_hash=h,
            question_text=body.question,
            knowledge_point=body.knowledge_point,
            script=body.script or body.question,
            avatar_id=body.avatar_id,
            voice_id=body.voice_id,
            status="pending",
        )
        db.add(row)
    else:
        row.status = "pending"
        row.error = None
        row.script = body.script or row.script or body.question
    await db.commit()
    await db.refresh(row)
    background.add_task(run_generation, row.id)
    return _row_out(row)
