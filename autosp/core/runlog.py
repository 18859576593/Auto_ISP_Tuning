# -*- coding: utf-8 -*-
"""运行历史（JSONL）：每轮迭代记录 参数/指标/目标值/产物路径，供复盘与可视化。"""
import json, os, time


class RunLog:
    def __init__(self, run_dir):
        os.makedirs(run_dir, exist_ok=True)
        self.path = os.path.join(run_dir, "history.jsonl")
        self.best = None  # (objective, record)

    def add(self, record: dict):
        record["time"] = time.strftime("%F %T")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.best is None or record["objective"] < self.best[0]:
            self.best = (record["objective"], record)
        return record

    def tail(self, n=5):
        if not os.path.exists(self.path):
            return []
        lines = open(self.path, encoding="utf-8").read().strip().splitlines()
        return [json.loads(x) for x in lines[-n:]]
