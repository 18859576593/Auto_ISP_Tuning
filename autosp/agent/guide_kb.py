# -*- coding: utf-8 -*-
"""调参指南知识库（L4 编排层的知识源，Phase 0 的产出物直接喂进来）。

guide.json 结构（平台各一份，人工+指南整理，LLM planner 与规则 planner 共用）:
{
  "tuning_order": ["ae","awb","ccm","gamma","lsc","nr","sharp","wdr"],  # 调参顺序(通识)
  "modules": {
    "nr": {
      "goal": "按增益分档控噪声, 平衡清晰度",
      "metrics": ["psnr","ssim"],          # 该模块对哪些指标敏感
      "rules": [
        {"symptom": "夜间噪声大", "action": "增大 nr 强度(高增益档)", "risk": "细节损失/拖影"}
      ]
    }
  }
}
"""
import json, os


class GuideKB:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.g = json.load(f)

    @property
    def order(self):
        return self.g.get("tuning_order", [])

    def module_info(self, module):
        return self.g.get("modules", {}).get(module, {})

    def rules_for(self, module):
        return self.module_info(module).get("rules", [])

    def prompt_context(self, modules=None):
        """渲染成给 LLM planner 的上下文文本。"""
        mods = modules or self.order
        lines = ["调参顺序: " + " -> ".join(self.order)]
        for m in mods:
            info = self.module_info(m)
            lines.append("[%s] 目标: %s; 指标: %s; 规则: %d 条" % (
                m, info.get("goal", ""), info.get("metrics", []), len(self.rules_for(m))))
            for r in self.rules_for(m):
                lines.append("  - 若%s → %s（风险: %s）" % (r.get("symptom"), r.get("action"), r.get("risk", "-")))
        return "\n".join(lines)
