# -*- coding: utf-8 -*-
"""目标函数组合器：多指标加权 -> 单个 objective（越小越好）。

原则: 客观指标为主（图卡可量化场景），VLM A/B 只做复核与破平（主观场景）。
"""
from autosp.eval import metrics as M


class ObjectiveCombiner:
    """weights 形如 {"psnr": -1.0, "ssim": -2.0, "mae": 0.02}
    负号表示"越大越好"的指标转成最小化问题。"""

    def __init__(self, ref_image: str, weights: dict):
        self.ref = ref_image
        self.weights = weights

    def __call__(self, captures: dict) -> dict:
        """captures: platform.capture() 的返回 {scene: path}。
        返回 {"metrics": {...}, "objective": float}"""
        scores = {}
        for scene, path in captures.items():
            for name, w in self.weights.items():
                raw = M.METRICS[name](self.ref, path)   # 原始指标（各自方向）
                scores["%s.%s" % (scene, name)] = raw
        objective = sum(self.weights[n.split(".", 1)[1]] * v for n, v in scores.items())
        return {"metrics": scores, "objective": objective}
