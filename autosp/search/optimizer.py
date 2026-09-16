# -*- coding: utf-8 -*-
"""搜索层：把参数空间交给优化器。内置零依赖实现，Optuna/OpenBox 可选后端。

内置:
  RandomSearch       纯随机，稳健基线
  CoordinateDescent  坐标下降（逐参数上下试探，对低维问题收敛快）
可选（import 成功才启用，Python3.14 目前装不上，接口留好）:
  OptunaTPE / OpenBoxBackend —— 借鉴 refs/ISP-AutoTuning 用 OpenBox(prf代理) 的做法
"""
import random


class OptimizerBase:
    def __init__(self, specs: dict):
        """specs: {key: ParamSpec}，只喂当前要搜的子空间。"""
        self.specs = specs
        self.best = None       # (objective, params)

    def suggest(self, history: list) -> dict:
        """给下一组候选参数。history: [{'params':..., 'objective':...}, ...]"""
        raise NotImplementedError

    def update(self, params: dict, objective: float):
        if self.best is None or objective < self.best[0]:
            self.best = (objective, dict(params))


class RandomSearch(OptimizerBase):
    def suggest(self, history):
        return {k: self._sample(s) for k, s in self.specs.items()}

    @staticmethod
    def _sample(s):
        if s.ptype == "int":
            return random.randint(int(s.lo), int(s.hi))
        if s.ptype == "enum":
            return random.choice(s.enum_values)
        return random.uniform(s.lo, s.hi)


class CoordinateDescent(OptimizerBase):
    """轮转每个参数：向上试探，改进则沿该方向继续；失败再向下试探；两向皆败换下一个参数。"""

    def __init__(self, specs, step_ratio=0.08):
        super().__init__(specs)
        self.step_ratio = step_ratio
        self._idx = 0
        self._phase = None   # None=待向上, "up_failed"=待向下

    def suggest(self, history):
        base = dict(self.best[1]) if self.best else {k: s.sample_default() for k, s in self.specs.items()}
        keys = list(self.specs)
        key = keys[self._idx % len(keys)]
        s = self.specs[key]
        span = (s.hi - s.lo) * self.step_ratio
        cur = base.get(key, s.sample_default())
        if self._phase == "up_failed":
            trial = self._clamp(s, cur - span)   # 向下试探
        else:
            trial = self._clamp(s, cur + span)   # 向上（或沿改进方向继续）
        base[key] = trial
        return base

    def update(self, params, objective):
        improved = self.best is None or objective < self.best[0]
        if improved:
            self._phase = None        # 沿新最优继续同方向
        elif self._phase == "up_failed":
            self._idx += 1            # 两个方向都失败，换参数
            self._phase = None
        else:
            self._phase = "up_failed" # 上探失败，下次向下
        super().update(params, objective)

    @staticmethod
    def _clamp(s, v):
        if s.ptype == "int":
            return int(max(s.lo, min(s.hi, round(v))))
        return max(s.lo, min(s.hi, v))


def make_optimizer(name: str, specs: dict) -> OptimizerBase:
    if name == "random":
        return RandomSearch(specs)
    if name == "coord":
        return CoordinateDescent(specs)
    if name == "optuna":
        try:
            import optuna  # noqa
            raise NotImplementedError("Optuna 后端接口已留，接入时实现 suggest 适配")
        except ImportError:
            print("[search] optuna 不可用，退回 coord")
            return CoordinateDescent(specs)
    if name == "openbox":
        print("[search] openbox 不支持当前 Python，退回 coord（借鉴其 prf 代理思想）")
        return CoordinateDescent(specs)
    raise KeyError("未知优化器: %s" % name)
