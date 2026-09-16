# -*- coding: utf-8 -*-
"""规划器（L4）：决定"下一步调哪个模块、什么方向"。指南为主，LLM 可选。

RulePlanner: 纯规则 —— 指标最差且指南里有映射的模块优先（零依赖，可跑）
LLMPlanner:  把 指南上下文+指标历史+平台自述 喂给大模型出决策（接口就绪，填 API 即用）
"""
from autosp.agent.guide_kb import GuideKB


class Plan:
    def __init__(self, module, direction, reason, params=None, iterations=6):
        self.module = module        # 下一步调的模块
        self.direction = direction # 方向描述（给人看）
        self.reason = reason
        self.params = params       # 参与搜索的参数键（None=该模块全部）
        self.iterations = iterations

    def __repr__(self):
        return "Plan(module=%s, dir=%s, iters=%d, reason=%s)" % (
            self.module, self.direction, self.iterations, self.reason[:40])


class RulePlanner:
    """规则版：按指南顺序轮转模块；方向 = 该模块关联指标里"距目标最差"的那个。"""

    def __init__(self, kb: GuideKB, metric_targets: dict = None):
        self.kb = kb
        self.targets = metric_targets or {}   # 如 {"psnr": 40.0, "ssim": 0.95}

    def next_plan(self, schema, history: list, platform_desc=""):
        order = [m for m in self.kb.order if schema.keys(m)]
        if not order:
            raise ValueError("指南顺序里的模块在 schema 中都不存在，请核对 schema/guide")
        last = history[-1]["module"] if history and "module" in history[-1] else None
        idx = (order.index(last) + 1) % len(order) if last in order else 0
        module = order[idx]
        info = self.kb.module_info(module)
        metrics = {k: v for k, v in (history[-1].get("metrics", {}) if history else {}).items()}
        gaps = []
        for name, target in self.targets.items():
            cur = [v for k, v in metrics.items() if k.endswith(name)]
            if cur:
                gaps.append((name, target - cur[-1]))
        direction = "优化 %s" % (gaps[0][0] if gaps else info.get("metrics", ["画质"]))
        reason = "指南顺序轮转 → %s（目标: %s）" % (module, info.get("goal", "画质提升"))
        return Plan(module, direction, reason)


class LLMPlanner:
    """LLM 版编排决策：指南+指标历史喂给大模型，输出结构化 Plan。失败自动回退 RulePlanner。

    配置: 环境变量 AUTOSP_LLM_BASE_URL/AUTOSP_LLM_API_KEY/AUTOSP_LLM_MODEL
          或 autosp/llm_config.json（见 llm_client.py 文档）
    """

    PROMPT = """你是资深 ISP 调参工程师。根据调参指南与指标历史，决定下一步调哪个模块、往哪个方向调。

【调参指南】
{guide_ctx}

【平台】{platform}
【指标历史(最近几轮, objective 越小越好)】
{history}

只输出一行 JSON，不要其他文字:
{{"module": "模块名(必须是指南顺序中存在的)", "direction": "方向(如 增大/减小/保持)", "reason": "一句话理由", "iterations": 6}}"""

    def __init__(self, kb: GuideKB, fallback: RulePlanner = None, client=None):
        self.kb = kb
        self.fallback = fallback or RulePlanner(kb)
        self.cli = client  # None 时惰性创建

    def _client(self):
        if self.cli is None:
            from autosp.agent.llm_client import LLMClient
            self.cli = LLMClient()
        return self.cli

    def next_plan(self, schema, history, platform_desc=""):
        order = self.kb.order
        hist_lines = []
        for r in history[-5:]:
            if "objective" in r:
                hist_lines.append("module=%s obj=%.4f metrics=%s params=%s" % (
                    r.get("module"), r["objective"],
                    {k: round(v, 3) for k, v in r.get("metrics", {}).items()},
                    r.get("params")))
        prompt = self.PROMPT.format(guide_ctx=self.kb.prompt_context(order),
                                    platform=platform_desc,
                                    history="\n".join(hist_lines) or "(首轮)")
        try:
            import json as _json
            text = self._client().chat([{"role": "user", "content": prompt}])
            text = text.strip().strip("`")
            if text.startswith("json"):
                text = text[4:]
            d = _json.loads(text[text.index("{"):text.rindex("}") + 1])
            module = d["module"]
            if module not in order or not schema.keys(module):
                raise ValueError("模块 %s 不在指南/schema 中" % module)
            return Plan(module, d.get("direction", ""), d.get("reason", ""),
                        iterations=int(d.get("iterations", 6)))
        except Exception as e:
            print("[LLMPlanner] 回退规则版: %s" % e)
            return self.fallback.next_plan(schema, history, platform_desc)
