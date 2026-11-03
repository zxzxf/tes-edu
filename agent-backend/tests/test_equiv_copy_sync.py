"""两份 ``equiv.py`` 必须逐字一致（agent-backend / web-backend）。

判分纯函数在两个后端各有一份副本：agent 侧用于编排/对话判分，web 侧用于
组卷发布与人工工单。二者一旦漂移，同一份作答会出现"Agent 判对、Web 判错"
的双口径事故，且没有任何单测会红——所以在这里显式锁死。
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agent-backend" / "app" / "core" / "equiv.py"
WEB = REPO / "web-backend" / "app" / "core" / "equiv.py"


def test_equiv_copies_are_identical():
    assert AGENT.is_file() and WEB.is_file(), "equiv.py 副本缺失"
    assert AGENT.read_bytes() == WEB.read_bytes(), (
        "agent-backend 与 web-backend 的 equiv.py 已漂移；"
        "请把改动同步到另一侧（两侧必须逐字一致）"
    )