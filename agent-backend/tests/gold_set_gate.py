"""M2-8 gold set 准确率门禁（CI 聚合口径）。

把 gold set 的「判分准确率」做成一个可度量的门禁指标：准确率低于阈值即
CI fail。阈值按方案书口径 —— 首月 ``0.95``（观察期）/ 稳定期 ``0.98``，
可用环境变量 ``GOLD_SET_MIN_ACCURACY`` 覆盖。

与 pytest 的关系（两层防线，不是二选一）
----------------------------------------
- ``tests/test_gold_set.py`` 的 parametrize 用例是**逐例硬约束**：任何一条红线
  回归都会让 pytest 直接变红。这是防「数据集变大后个别红线失败被平均值稀释」
  的底线，任何情况下都不应放松。
- 本脚本给的是**聚合口径 + 阈值策略**：把通过率量化、列出失败清单、写进
  Actions Step Summary，让「准确率掉了多少、掉在哪类写法」一眼可见，
  也便于把 95% → 98% 的收敛过程留痕。

为什么还要校验最少样例数
------------------------
只校验通过率的话，把 gold set 删到 3 题、对 3 题就是 100%。所以同时校验
总数 ``>= MIN_CASES``（默认 30，对齐卡面「30~50 题评测集」），防止用空集/
小集刷绿。

用法
----
    python tests/gold_set_gate.py                       # 默认阈值 0.98
    GOLD_SET_MIN_ACCURACY=0.95 python tests/gold_set_gate.py    # 首月宽松
    python tests/gold_set_gate.py --min-accuracy 1.0 --min-cases 50
    python tests/gold_set_gate.py --cases path/to/other_cases.py

退出码
------
    0 通过；1 准确率低于阈值；2 样例数不足；3 用例文件解析失败
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Any, List, Sequence, Tuple

# 允许从仓库任意位置调用（`python tests/gold_set_gate.py` 或 -m 形式）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.equiv import blank_equivalent, choice_equivalent  # noqa: E402

DEFAULT_CASES_FILE = Path(__file__).with_name("test_gold_set.py")
DEFAULT_MIN_ACCURACY = 0.98
DEFAULT_MIN_CASES = 30


class CaseFileError(RuntimeError):
    """gold set 用例文件无法解析。"""


def load_cases(path: Path) -> List[Tuple[Any, ...]]:
    """从用例文件里取出 ``CASES`` 列表。

    刻意**不 import** 那个文件：它顶部 ``import pytest``，而门禁脚本不应把
    pytest 变成运行前置（本地没装 pytest 也要能跑门禁）。这里改用 AST 读取
    模块级 ``OPTS`` / ``CASES`` 两个字面量，``CASES`` 里对 ``OPTS`` 的引用用
    受限 ``eval``（只注入 OPTS）解析。
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 文件缺失属环境问题
        raise CaseFileError(f"读取用例文件失败：{path}（{exc}）") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise CaseFileError(f"用例文件语法错误：{path}（{exc}）") from exc

    nodes = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("OPTS", "CASES"):
                    nodes[target.id] = node.value
    if "CASES" not in nodes:
        raise CaseFileError(f"用例文件里没有 CASES 定义：{path}")

    opts = {}
    if "OPTS" in nodes:
        try:
            opts = ast.literal_eval(nodes["OPTS"])
        except (ValueError, TypeError) as exc:
            raise CaseFileError(f"OPTS 不是字面量：{path}（{exc}）") from exc

    expr = ast.Expression(body=nodes["CASES"])
    ast.fix_missing_locations(expr)
    try:
        code = compile(expr, str(path), "eval")
        cases = eval(code, {"OPTS": opts, "__builtins__": {}}, {})  # noqa: S307 - 仅自家用例文件
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都归为用例文件问题
        raise CaseFileError(f"CASES 求值失败：{path}（{exc}）") from exc
    return list(cases)


def _judge(case: Sequence[Any]) -> Any:
    """跑单条用例，返回实际判定结果。"""
    _name, kind, ref, stu, opts, _exp, multi = case
    if kind == "choice":
        return choice_equivalent(stu, ref, multi=multi, options=opts)
    return blank_equivalent(stu, ref)


def evaluate(cases: Sequence[Sequence[Any]]) -> Tuple[int, List[Tuple[str, Any, Any]]]:
    """返回 (通过数, 失败清单[(题名, 期望, 实际)])，并校验用例结构。"""
    failures: List[Tuple[str, Any, Any]] = []
    for idx, case in enumerate(cases):
        if len(case) != 7:
            raise CaseFileError(f"第 {idx + 1} 条用例不是 7 元组：{case!r}")
        name, _kind, _ref, _stu, _opts, expected, _multi = case
        got = _judge(case)
        if got != expected:
            failures.append((str(name), expected, got))
    return len(cases) - len(failures), failures


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _write_step_summary(
    total: int,
    passed: int,
    accuracy: float,
    min_accuracy: float,
    min_cases: int,
    failures: Sequence[Tuple[str, Any, Any]],
) -> None:
    """把结果写进 GitHub Actions Step Summary（本地运行时静默跳过）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    ok = accuracy >= min_accuracy and total >= min_cases
    lines = [
        "## Gold set 判分准确率门禁（M2-8）",
        "",
        f"- 结果：**{'PASS' if ok else 'FAIL'}**",
        f"- 准确率：**{_fmt_pct(accuracy)}**（阈值 {_fmt_pct(min_accuracy)}）",
        f"- 用例数：{total}（下限 {min_cases}）｜通过 {passed}｜失败 {total - passed}",
        "",
    ]
    if failures:
        lines += ["| 题名 | 期望 | 实际 |", "|---|---|---|"]
        lines += [f"| {name} | `{exp!r}` | `{got!r}` |" for name, exp, got in failures]
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:  # pragma: no cover - summary 写失败不该影响门禁结论
        pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2-8 gold set 准确率门禁")
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_FILE),
        help=f"用例文件路径（默认 {DEFAULT_CASES_FILE.name}）",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=float(os.environ.get("GOLD_SET_MIN_ACCURACY", DEFAULT_MIN_ACCURACY)),
        help="最低准确率（默认取 GOLD_SET_MIN_ACCURACY，否则 0.98）",
    )
    parser.add_argument(
        "--min-cases",
        type=int,
        default=int(os.environ.get("GOLD_SET_MIN_CASES", DEFAULT_MIN_CASES)),
        help="最少用例数，防止删样例刷绿（默认 30）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    cases_path = Path(args.cases)
    try:
        cases = load_cases(cases_path)
        passed, failures = evaluate(cases)
    except CaseFileError as exc:
        print(f"[gold-set-gate] ERROR {exc}", file=sys.stderr)
        return 3

    total = len(cases)
    accuracy = (passed / total) if total else 0.0
    min_accuracy = args.min_accuracy
    min_cases = args.min_cases

    print(
        f"[gold-set-gate] total={total} passed={passed} failed={len(failures)} "
        f"accuracy={_fmt_pct(accuracy)} threshold={_fmt_pct(min_accuracy)} "
        f"min_cases={min_cases}"
    )
    for name, expected, got in failures:
        print(f"[gold-set-gate] MISMATCH {name}: expected {expected!r}, got {got!r}")

    _write_step_summary(total, passed, accuracy, min_accuracy, min_cases, failures)

    if total < min_cases:
        print(
            f"[gold-set-gate] FAIL 用例数 {total} < 下限 {min_cases}"
            "（删样例不能刷绿，请补回评测题）",
            file=sys.stderr,
        )
        return 2
    if accuracy < min_accuracy:
        print(
            f"[gold-set-gate] FAIL 准确率 {_fmt_pct(accuracy)} < 阈值 {_fmt_pct(min_accuracy)}",
            file=sys.stderr,
        )
        return 1
    print("[gold-set-gate] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
