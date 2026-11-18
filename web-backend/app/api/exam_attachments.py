"""M1-6：学生答题附件（解答题图片上传）。

App 侧在 subjective 题作答页拍照/选图，POST 到这里换回一个相对 URL，
URL 以 ``[图](url)`` 追加进 student_answer 文本随提交落库——判分主链路
零改动（人工批改本来就看文本，工单 4000 字符截断足以容纳若干 URL）。

鉴权与隔离口径（对齐 explanation_video 的教训，网关 jwt_c 挡裸直链）：
- 上传/取图都走 /api/student/ 前缀 + 学生角色，网关缺 Authorization 必 401；
- 对象名是 22 位随机 token，不可枚举；tenant/student 记在旁车 json 供审计;
- 大小/MIME 双闸门（10MB / jpeg|png|webp），超限 413、非法类型 415；
- 本地私有目录（默认 data/exam_attachments），不进静态挂载，只能经本路由取流。
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

from app.core.config import settings
from app.core.rbac import Role
from app.core.security import require_role

log = logging.getLogger("exam-attachments")

router = APIRouter(tags=["exam-attachments"])

_MAX_BYTES = 10 * 1024 * 1024
_ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# 随机 token，无用户可控内容参与文件名——路径穿越在文件名层面先天不可能
_TOKEN_RE = re.compile(r"^[a-f0-9]{22}$")


def _dir() -> Path:
    p = Path(settings.exam_attachment_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.post("/exam-attachments", summary="上传答题附图，返回可嵌入作答文本的 URL")
async def upload_attachment(
    file: UploadFile = File(...),
    user=Depends(require_role(Role.STUDENT)),
):
    ctype = (file.content_type or "").lower()
    if ctype not in _ALLOWED:
        raise HTTPException(status_code=415, detail=f"不支持的图片类型：{ctype or '未知'}")
    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="图片超过 10MB 上限")
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    token = secrets.token_hex(11)
    base = _dir() / f"{token}{_ALLOWED[ctype]}"
    base.write_bytes(data)
    meta = {
        "student_id": user.get("user_id"),
        "tenant_id": user.get("tenant_id"),
        "size": len(data),
        "content_type": ctype,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    (_dir() / f"{token}.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    url = f"/api/student/exam-attachments/{token}"
    return {"url": url, "size": len(data), "content_type": ctype}


@router.get("/exam-attachments/{token}", summary="取回答题附图（学生鉴权 + token 不可枚举）")
async def get_attachment(token: str, user=Depends(require_role(Role.STUDENT))):
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="attachment not found")
    base = _dir()
    for ext in _ALLOWED.values():
        p = base / f"{token}{ext}"
        if p.exists():
            meta = {}
            mj = base / f"{token}.json"
            if mj.exists():
                try:
                    meta = json.loads(mj.read_text("utf-8"))
                except Exception:  # noqa: BLE001 旁车坏了不挡取图
                    meta = {}
            return Response(
                content=p.read_bytes(),
                media_type=meta.get("content_type") or "image/jpeg",
                headers={"Cache-Control": "private, no-store"},
            )
    raise HTTPException(status_code=404, detail="attachment not found")
