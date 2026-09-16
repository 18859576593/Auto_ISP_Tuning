# -*- coding: utf-8 -*-
"""平台适配层抽象。换平台 = 实现以下接口 + 提供一份 param_schema.json。

生命周期约定:
  set_params(dict) -> 生效（在线平台立即，离线平台写配置待重启）
  capture(scene)   -> 采图，返回 {name: 图片路径}
  export_profile() -> 调优结果落成平台原生格式（TXW: 驱动参数表 patch / Hailo: JSON+hash / 海思: xml）
"""
import os
from autosp.core.schema import ParamSchema


class AbstractTuningPlatform:
    name = "abstract"
    schema_path = None          # platforms/<name>/param_schema.json
    online = True               # True=在线调参(秒级闭环) False=写配置重启生效(分钟级)
    params_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "platforms")

    def get_schema(self) -> ParamSchema:
        return ParamSchema.load(self.schema_path)

    # ---- 三个必须实现的接口 ----
    def set_params(self, params: dict):
        """写参数。实现方必须: 校验(调 self.get_schema().validate) -> 落快照策略由上层负责。"""
        raise NotImplementedError

    def capture(self, scene: str = "default") -> dict:
        """采图。返回 {"<scene>": 本地图片路径}。"""
        raise NotImplementedError

    def export_profile(self, params: dict, out_path: str):
        """参数固化为平台原生格式。"""
        raise NotImplementedError

    # ---- 可选: 平台自述（给 LLM planner 的上下文） ----
    def describe(self) -> str:
        return "%s (online=%s)" % (self.name, self.online)
