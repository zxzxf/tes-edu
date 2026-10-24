"""S6 评测脚手架（T4）：金标准 → judge v2 → 三张表 + κ + 归档。

用法：
    # 脚手架自测（不连库、不调 LLM，合成数据确定性判定）：
    python scripts/eval_attribution.py --gold eval/gold_standard_template.csv \
        --batch 2026-脚手架自测 --mock

    # 真实评测（需 106 隧道 + LLM 配置，在 agent-backend 目录跑）：
    python scripts/eval_attribution.py --gold eval/gold_standard.csv --batch 2026-试点校-批次1

归档：<out>/<batch>/ 下 gold_standard.csv（原始副本）、report.md（三张表）、
results.json（逐行判定明细 + 指标）。
指标口径与阈值见 app/core/eval_metrics.py 与 eval/README.md。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import eval_metrics  # noqa: E402


async def judge_one(row: dict, mock: bool, noise: float) -> dict:
    """单行判定：mock 模式走确定性假判定器；真实模式调 judge v2。"""
    if mock:
        return eval_metrics.mock_judge(row, noise)
    from app.core import judge_v2  # 延迟导入：mock 模式不加载 LLM/Kafka 依赖

    context = {"chunks": [{"content_hash": row.get("content_hash")}]}
    return await judge_v2.judge(
        "", row.get("学生作答过程") or "", context
    )


async def run_eval(
    gold_path: str,
    batch: str,
    out_root: str,
    mock: bool = False,
    noise: float = 0.0,
) -> dict:
    rows = eval_metrics.load_gold_csv(gold_path)
    if not rows:
        raise SystemExit(f"金标准无数据行: {gold_path}")

    results: list[dict] = []
    for i, row in enumerate(rows, start=1):
        res = await judge_one(row, mock, noise)
        results.append(res)
        tag = "MOCK" if mock else res.get("attribution_source", "?")
        print(f"  [{i}/{len(rows)}] {row.get('题型', '?'):<4} {tag:<8} "
              f"step={res.get('first_wrong_step')} et={res.get('error_type')}")

    obj = eval_metrics.objective_map_rate(rows, results)
    step = eval_metrics.first_wrong_step_accuracy(rows, results)
    et = eval_metrics.error_type_metrics(rows, results)
    kappa = eval_metrics.rater_kappa(rows)
    report = eval_metrics.render_report(batch, gold_path, len(rows), obj, step, et, kappa)

    # 归档：原始金标准副本 + report.md + results.json
    out_dir = Path(out_root) / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(gold_path, out_dir / "gold_standard.csv")
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    details = {
        "batch": batch,
        "gold": str(gold_path),
        "mock": mock,
        "noise": noise,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"objective": obj, "first_wrong_step": step,
                    "error_type": et, "rater_kappa": kappa},
        "rows": [
            {
                "content_hash": r.get("content_hash"),
                "题型": r.get("题型"),
                "gold_step": eval_metrics.to_int(r.get("金标准首错步骤")),
                "gold_error_type": r.get("金标准错因"),
                "judge": res,
            }
            for r, res in zip(rows, results)
        ],
    }
    (out_dir / "results.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== S6 评测摘要（{batch}，{len(rows)} 行，{'MOCK 自测' if mock else '真实 judge v2'}）===")
    obj_rate = "—" if obj["rate"] is None else f"{obj['rate']*100:.1f}%"
    step_acc = "—" if step["accuracy"] is None else f"{step['accuracy']*100:.1f}%"
    et_acc = "—" if et["accuracy"] is None else f"{et['accuracy']*100:.1f}%"
    et_unk = "—" if et["unknown_rate"] is None else f"{et['unknown_rate']*100:.1f}%"
    print(f"  表一 客观题查表命中率 : {obj['map_hit']}/{obj['total']} = {obj_rate}")
    print(f"  表二 首错步骤对齐     : {step['hit']}/{step['total']} = {step_acc}"
          f"（未贴过程 {step['skipped_need_process']} 行不计）")
    print(f"  表三 错因分类准确率   : {et['hit']}/{et['labeled']} = {et_acc}"
          f"；unknown 率 {et['unknown']}/{et['judged']} = {et_unk}")
    print(f"  附录 评分人 κ         : n={kappa['n']} po={kappa['po']} κ={kappa['kappa']}")
    print(f"\n归档目录: {out_dir}")
    return details


async def main() -> None:
    ap = argparse.ArgumentParser(description="S6 归因评测脚手架（T4）")
    ap.add_argument("--gold", required=True, help="金标准 CSV 路径")
    ap.add_argument("--batch", required=True, help="批次名（归档子目录名）")
    ap.add_argument("--out", default="eval", help="归档根目录（默认 eval/）")
    ap.add_argument("--mock", action="store_true",
                    help="脚手架自测模式：确定性假判定，不连库不调 LLM")
    ap.add_argument("--mock-noise", type=float, default=0.0,
                    help="mock 扰动比例 0~1（按 content_hash 稳定扰动，验证指标会变）")
    args = ap.parse_args()
    if not os.path.exists(args.gold):
        raise SystemExit(f"金标准文件不存在: {args.gold}")
    await run_eval(args.gold, args.batch, args.out, mock=args.mock, noise=args.mock_noise)


if __name__ == "__main__":
    asyncio.run(main())
