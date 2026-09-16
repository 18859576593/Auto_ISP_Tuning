# -*- coding: utf-8 -*-
"""VLM A/B 对比评审（L2 评价层的主观部分）。

关键设计（来自调研结论）:
- VLM 只做【A/B 对比】不做绝对打分 —— 相对判断远比绝对可靠 (参照 Diff-CPIQA 思路)
- 锚点机制: 提问时附上参考标杆帧，问"哪张更接近锚点"
- 后端可插拔: mock(确定性启发式, 跑CI) / http(任意 VLM 服务) / 后续接本地多模态模型

用法:
    judge = VLMJudge(backend="mock")
    verdict = judge.compare(img_a, img_b, question="哪张噪声更少、画面更干净?")
    # -> {"choice": "A"|"B"|"tie", "confidence": 0.0~1.0, "reason": "..."}
"""
import hashlib


class VLMJudge:
    BACKENDS = ("mock", "http", "openai")

    def __init__(self, backend="mock", endpoint=None):
        self.backend = backend
        self.endpoint = endpoint

    def compare(self, img_a: str, img_b: str, question: str, anchor: str = None):
        if self.backend == "mock":
            return self._mock(img_a, img_b, question)
        if self.backend == "openai":
            return self._openai(img_a, img_b, question, anchor)
        return self._http(img_a, img_b, question, anchor)

    # ---- mock: 确定性占位（演示/CI 用，真实接入后替换） ----
    def _mock(self, img_a, img_b, question):
        h = int(hashlib.md5((img_a + img_b + question).encode()).hexdigest()[:8], 16)
        choice = ("A", "B", "tie")[h % 3]
        return {"choice": choice, "confidence": 0.5, "reason": "mock 后端占位判断"}

    # ---- openai 兼容: 走 llm_client(云端API/Ollama 通用), A/B 对比提问 ----
    def _openai(self, img_a, img_b, question, anchor):
        import json as _json, re
        from autosp.agent.llm_client import LLMClient
        cli = LLMClient()
        imgs = [LLMClient.b64(img_a), LLMClient.b64(img_b)]
        heads = ["图A", "图B"]
        if anchor:
            imgs.insert(0, LLMClient.b64(anchor))
            heads.insert(0, "参考标杆")
        q = ("%s。第一张是%s，第二张是%s。"
             "只输出JSON: {\"choice\":\"A\"或\"B\"或\"tie\",\"confidence\":0~1,\"reason\":\"一句话\"}"
             % (question, heads[-2], heads[-1]))
        text = cli.chat_vision(imgs, q)
        m = re.search(r"\{.*\}", text, re.S)
        d = _json.loads(m.group(0))
        d.setdefault("choice", "tie")
        d.setdefault("confidence", 0.5)
        d.setdefault("reason", text[:100])
        return d

    # ---- http: 自定义 VLM 服务（POST 图片base64+问题，返回 JSON） ----
    def _http(self, img_a, img_b, question, anchor):
        import json, base64, urllib.request
        def b64(p):
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode()
        payload = json.dumps({
            "image_a": b64(img_a), "image_b": b64(img_b),
            "anchor": b64(anchor) if anchor else None, "question": question,
        }).encode()
        req = urllib.request.Request(self.endpoint, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
