# -*- coding: utf-8 -*-
"""Phase 1 演示：离线闭环自动调参（fast-OpenISP 仿真平台）。

流程: RulePlanner(指南) → CoordinateDescent(nlm.h / eeh.edge_gain) → 平台跑管线
      → PSNR+SSIM 对参考图评价 → 快照/历史/固化导出
预期: 10 轮左右 objective 收敛（-PSNR - 2*SSIM 越大越好→最小化其负值）
"""
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autosp.platform import get_platform
from autosp.eval.objectives import ObjectiveCombiner
from autosp.agent.guide_kb import GuideKB
from autosp.agent.planner import RulePlanner
from autosp.agent.runner import TuningSession


def main():
    pf = get_platform("offline_sim")
    print("平台:", pf.describe())

    objective = ObjectiveCombiner(
        ref_image=pf.ref_image,
        weights={"psnr": -1.0, "ssim": -2.0},   # 最小化 -PSNR - 2*SSIM
    )
    kb = GuideKB(os.path.join(ROOT, "platforms", "offline_sim", "guide.json"))
    planner = RulePlanner(kb, metric_targets={"psnr": 45.0, "ssim": 0.98})

    session = TuningSession(pf, objective, planner, optimizer_name="coord")
    print("运行目录:", session.run_dir)
    t0 = time.time()
    session.run(rounds=2, iters_per_round=5)
    print("总耗时 %.1fs, 历史: %s" % (time.time() - t0, os.path.join(session.run_dir, "history.jsonl")))


if __name__ == "__main__":
    main()
