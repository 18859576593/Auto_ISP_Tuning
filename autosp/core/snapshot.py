# -*- coding: utf-8 -*-
"""参数快照与回滚。每次写参数前落一份快照，出问题一键回滚（对齐 MCU profile 教训）。"""
import json, os, time


class SnapshotManager:
    def __init__(self, run_dir):
        self.dir = os.path.join(run_dir, "snapshots")
        os.makedirs(self.dir, exist_ok=True)

    def save(self, params: dict, tag=""):
        name = "snap_%s_%s.json" % (time.strftime("%H%M%S"), tag or "auto")
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"time": time.strftime("%F %T"), "params": params}, f,
                      ensure_ascii=False, indent=2)
        return path

    def list(self):
        return sorted(f for f in os.listdir(self.dir) if f.endswith(".json"))

    def load(self, name=None):
        files = self.list()
        if not files:
            raise FileNotFoundError("无快照")
        name = name or files[-1]
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return json.load(f)["params"]

    def rollback(self, platform, name=None):
        """回滚：加载快照并写回平台。"""
        params = self.load(name)
        platform.set_params(params)
        return params
