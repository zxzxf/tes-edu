"""BYOM T-M1 本地单测：sqlite 内存库，验证模型建表 / 密钥加解密往返 / 脱敏 / CRUD。

不依赖 Postgres、不依赖任何外部 LLM 配额；仅用本地 crypto 与 sqlite。

运行：
    cd web-backend && python byom_tm1_check.py
"""
import asyncio
import os
import tempfile

# ---- 必须在 import app 之前设置（config / vendor_crypto 在调用时读环境变量）----
_DB_FILE = os.path.join(tempfile.gettempdir(), "_byom_tm1_test.db")
if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_FILE}"
# 测试用 32 字节主密钥（hex），仅本测试使用
os.environ["VENDOR_CREDENTIAL_KEY"] = os.urandom(32).hex()

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.course_kb import Base
from app.models import byom as byom_model  # noqa: F401 注册表
from app.services import byom as byom_svc


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    fails = []

    # 1) 密钥加解密往返
    raw = "sk-tesedu-1234567890abcdef"
    blob = byom_svc.vc.encrypt(raw)
    back = byom_svc.decrypt_api_key(blob)
    if back != raw:
        fails.append(f"密钥往返失败: {back!r} != {raw!r}")
    if blob == raw.encode():
        fails.append("明文直接落库（加密未生效）")

    # 2) 脱敏：仅末 4 位
    if byom_svc.mask_key(raw) != "****cdef":
        fails.append(f"脱敏错误: {byom_svc.mask_key(raw)}")
    if byom_svc.mask_key("") != "":
        fails.append("空密钥脱敏应返回空串")

    # 3) upsert + 查询 + 解密
    async with Session() as db:
        await byom_svc.upsert_config(
            db,
            tenant_id="school_a",
            provider="aliyun",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_plain=raw,
            model="qwen-max",
            embed_model="text-embedding-v2",
            rag_top_k=6,
        )
        row = await byom_svc.get_config(db, "school_a")
        if row is None:
            fails.append("upsert 后查不到 school_a")
        else:
            if row.provider != "aliyun":
                fails.append(f"provider 错误: {row.provider}")
            if row.model != "qwen-max":
                fails.append(f"model 错误: {row.model}")
            if row.rag_top_k != 6:
                fails.append(f"rag_top_k 错误: {row.rag_top_k}")
            if row.enabled is not True:
                fails.append("enabled 应为 True")
            # 落库的是密文，解密后才等于明文
            if row.api_key_enc == raw.encode():
                fails.append("api_key_enc 明文落库")
            if byom_svc.decrypt_api_key(row.api_key_enc) != raw:
                fails.append("落库后解密不等于原文")

    # 4) default 租户占位（enabled=False, 加密空串）
    async with Session() as db:
        await byom_svc.upsert_config(
            db,
            tenant_id="__global__",
            provider="custom",
            base_url="",
            api_key_plain="",
            model="",
            enabled=False,
        )
        g = await byom_svc.get_config(db, "__global__")
        if g is None or g.enabled is not False:
            fails.append("__global__ 占位行缺失或 enabled 不为 False")
        elif byom_svc.decrypt_api_key(g.api_key_enc) != "":
            fails.append("占位行解密应得空串")

    # 5) list 返回两条
    async with Session() as db:
        rows = await byom_svc.list_configs(db)
        if len(rows) != 2:
            fails.append(f"list_configs 数量应为 2，实际 {len(rows)}")

    await engine.dispose()
    if os.path.exists(_DB_FILE):
        os.remove(_DB_FILE)

    if fails:
        print("BYOM_TM1_TESTS_FAIL")
        for f in fails:
            print(" -", f)
        raise SystemExit(1)
    print("ALL_BYOM_TM1_TESTS_PASS")


if __name__ == "__main__":
    asyncio.run(main())
