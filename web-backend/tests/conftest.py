"""pytest 引导：把 web-backend 根目录加入 sys.path，使 `import app` 可用。

测试均为纯逻辑、不连数据库、不依赖外部服务（DB/ACR/Kafka 均不触碰）。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
