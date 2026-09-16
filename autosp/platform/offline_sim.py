# -*- coding: utf-8 -*-
"""离线仿真平台（Phase 1）：把 fast-OpenISP 当作被调平台，验证整套闭环框架。

借鉴: refs/ISP-AutoTuning/run_auto_tuning.py（OpenBox+LPIPS 的最小闭环）
本实现差异: 参数/指标/优化器全部走 autosp 抽象，LPIPS 可选、默认 PSNR+SSIM。
"""
import os, sys, copy, yaml
import numpy as np
import cv2

from autosp.platform.base import AbstractTuningPlatform

REFS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "refs")
_AUTO_ROOT = os.path.join(REFS, "ISP-AutoTuning")
sys.path.insert(0, _AUTO_ROOT)


class OfflineSimPlatform(AbstractTuningPlatform):
    name = "offline_sim"
    online = True   # 仿真当然是"在线"的：秒级改参生效

    def __init__(self, base_config=None, raw_path=None, ref_image=None, workdir=None):
        self.schema_path = os.path.join(self.params_dir, "offline_sim", "param_schema.json")
        self.base_config = base_config or os.path.join(
            _AUTO_ROOT, "fast_OpenISP", "configs", "test.yaml")
        self.raw_path = raw_path or os.path.join(
            _AUTO_ROOT, "fast_OpenISP", "raw", "test.RAW")
        self.ref_image = ref_image or os.path.join(
            _AUTO_ROOT, "fast_OpenISP", "output", "ref.png")
        self.workdir = workdir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "runs", "offline_sim")
        os.makedirs(self.workdir, exist_ok=True)
        self._current = {}      # 当前已应用的扁平参数
        self._lazy = {}

    # ---- 依赖按需加载（首次调用时 import，加快框架启动） ----
    def _pipe(self):
        if "pipe" not in self._lazy:
            from fast_OpenISP.pipeline import Pipeline
            from fast_OpenISP.utils.yacs import Config
            with open(self.base_config, encoding="utf-8") as f:
                cfg_dict = yaml.safe_load(f)
            cfg_dict = self._apply_flat(cfg_dict, self._current)
            self._lazy["cls"] = (Pipeline, Config)
            self._lazy["pipe"] = Pipeline(Config(cfg_dict))
        return self._lazy["pipe"]

    @staticmethod
    def _apply_flat(cfg_dict: dict, flat: dict) -> dict:
        """扁平参数 {'nlm.h': 100} 合入嵌套 yaml dict {'nlm': {'h': ...}}。"""
        for key, val in flat.items():
            mod, _, name = key.partition(".")
            cfg_dict.setdefault(mod, {})
            cfg_dict[mod][name] = val
        return cfg_dict

    # ---- 三接口实现 ----
    def set_params(self, params: dict):
        ok, msg = self.get_schema().validate(params)
        if not ok:
            raise ValueError(msg)
        self._current.update(params)
        self._lazy.pop("pipe", None)   # 参数变了，pipeline 重建

    def capture(self, scene: str = "default") -> dict:
        pipe = self._pipe()
        with open(self.base_config, encoding="utf-8") as f:
            cfg_dict = yaml.safe_load(f)
        cfg_dict = self._apply_flat(cfg_dict, self._current)
        from fast_OpenISP.utils.yacs import Config
        bayer = np.fromfile(self.raw_path, dtype="uint16").reshape(
            (cfg_dict["hardware"]["raw_height"], cfg_dict["hardware"]["raw_width"]))
        data, _ = pipe.execute(bayer)
        out = os.path.join(self.workdir, "cap_%s.png" % scene)
        cv2.imwrite(out, cv2.cvtColor(data["output"], cv2.COLOR_RGB2BGR))
        return {scene: out}

    def export_profile(self, params: dict, out_path: str):
        with open(self.base_config, encoding="utf-8") as f:
            cfg_dict = yaml.safe_load(f)
        cfg_dict = self._apply_flat(cfg_dict, params)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg_dict, f, allow_unicode=True, sort_keys=False)
        return out_path

    def describe(self):
        return ("离线仿真平台(fast-OpenISP)：raw=%s，参考图=%s。"
                "用于 Phase1 框架验证，参数结构与真实平台同构。"
                % (os.path.basename(self.raw_path), os.path.basename(self.ref_image)))
