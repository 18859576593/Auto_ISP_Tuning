# -*- coding: utf-8 -*-
"""Hailo-15H 适配器（Phase 2 骨架，跨平台复用性验证用）。

平台特性（来自前期调研，community.hailo.ai + GitHub hailo-media-library）:
- 参数形态: profile 目录 iq_settings.json / 3aconfig.json（JSON 天然好提取）
- 生效方式: 离线型 —— 改 JSON 后需重算 content_hash（Profile Manager）并重启加载
  => online=False，闭环走"离线 raw 仿真搜参 + 少量实机验证"两段式
- 采图: GStreamer 抓流 / v4l2；raw 抓取用于离线仿真输入
- 增益链: maxSensorAgain / maxSensorDgain / MaxIspDgain 线性倍数、priority 表

TODO（拿到 Developer Zone 文档权限后填实）:
  [ ] set_params: 写 iq_settings.json 字段 + 调 Profile Manager 重算 hash
  [ ] capture: gst-launch 抓帧 / RTSP 拉流截图
  [ ] offline_raw: 板端抓 raw（isp bypass/sensor_only 模式）喂给离线仿真
"""
from autosp.platform.base import AbstractTuningPlatform


class Hailo15Platform(AbstractTuningPlatform):
    name = "hailo15h"
    online = False   # 离线型平台：分钟级闭环，走两段式

    PROFILE_FIELDS = {
        "iq_settings.json": ["denoise", "hdr.dol", "automatic_algorithms"],
        "3aconfig.json": ["AdaptiveAe.maxSensorAgain", "AdaptiveAe.maxSensorDgain",
                          "AdaptiveAe.MaxIspDgain", "AdaptiveAe.priority"],
    }

    def __init__(self, profile_dir=None, dry_run=True):
        import os
        self.schema_path = os.path.join(self.params_dir, "hailo15h", "param_schema.json")
        self.profile_dir = profile_dir
        self.dry_run = dry_run
        self._current = {}

    def set_params(self, params: dict):
        ok, msg = self.get_schema().validate(params)
        if not ok:
            raise ValueError(msg)
        self._current.update(params)
        if self.dry_run:
            print("[Hailo15 dry-run] 需重写 profile JSON + Profile Manager 重算 hash: %s" % params)
            return
        raise NotImplementedError("等待 Hailo Developer Zone 文档（见文件头 TODO）")

    def capture(self, scene="default"):
        if self.dry_run:
            print("[Hailo15 dry-run] capture scene=%s" % scene)
            return {}
        # TODO: gst-launch-1.0 ... ! pngfilesink 或 v4l2-ctl --stream-mmap
        raise NotImplementedError

    def export_profile(self, params: dict, out_path: str):
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("// Hailo profile patch（由 autosp 生成，需 Profile Manager 重算 content_hash）\n")
            f.write("params = %r\n" % params)
        return out_path
