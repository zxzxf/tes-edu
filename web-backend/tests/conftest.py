"""pytest 引导：把 web-backend 根目录加入 sys.path，使 `import app` 可用。

测试均为纯逻辑、不连数据库、不依赖外部服务（DB/ACR/Kafka 均不触碰）。
（2026-11-20 补）：TestClient 会触发 main.py lifespan——股票盯盘 worker 随进程
起循环并周期开 Session 读写，与测试 fixture 的 sqlite 引擎互相踩 commit，
造成跨文件时序性失败（实测 push_hub 单跑挂、搭 stock 跑过）。
全局 env 禁用，测试内 tick_once 仍手动调用覆盖 worker 逻辑。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("STOCK_WORKER_ENABLED", "false")
