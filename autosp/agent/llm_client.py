# -*- coding: utf-8 -*-
"""LLM/VLM 客户端（OpenAI 兼容格式，一个实现通吃云端 API 与本地 Ollama/vLLM）。

配置优先级: 环境变量 > autosp/llm_config.json > 无(未配置时抛错提示)
  AUTOSP_LLM_BASE_URL  如 https://open.bigmodel.cn/api/paas/v4 (智谱)
                       或 http://localhost:11434/v1 (Ollama)
  AUTOSP_LLM_API_KEY   云端密钥; 本地服务填任意非空串
  AUTOSP_LLM_MODEL     如 glm-4v-flash / qwen2.5-vl-7b / gpt-4o-mini

用法:
  from autosp.agent.llm_client import LLMClient, llm_configured
  if llm_configured():
      cli = LLMClient()
      text = cli.chat([{"role":"user","content":"..."}])
      ans  = cli.chat_vision([img1_b64, img2_b64], "哪张噪声更少? A 还是 B?")
"""
import os, json, base64, urllib.request

_CONF_ENV = {"base_url": "AUTOSP_LLM_BASE_URL", "api_key": "AUTOSP_LLM_API_KEY", "model": "AUTOSP_LLM_MODEL"}
_CONF_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llm_config.json")


def load_config():
    cfg = {}
    if os.path.exists(_CONF_FILE):
        try:
            cfg = json.load(open(_CONF_FILE, encoding="utf-8"))
        except Exception:
            pass
    for k, env in _CONF_ENV.items():
        v = os.environ.get(env)
        if v:
            cfg[k] = v
    return cfg


def llm_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("base_url") and cfg.get("model"))


class LLMClient:
    def __init__(self, base_url=None, api_key=None, model=None, timeout=60):
        cfg = load_config()
        self.base_url = (base_url or cfg.get("base_url", "")).rstrip("/")
        self.api_key = api_key or cfg.get("api_key", "EMPTY")
        self.model = model or cfg.get("model")
        self.timeout = timeout
        if not (self.base_url and self.model):
            raise RuntimeError(
                "LLM 未配置: 设环境变量 AUTOSP_LLM_BASE_URL/AUTOSP_LLM_MODEL(+KEY), "
                "或建 autosp/llm_config.json {\"base_url\":..., \"api_key\":..., \"model\":...}")

    # ---- 基础请求 ----
    def _post(self, payload):
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read().decode())
        return out["choices"][0]["message"]["content"]

    def chat(self, messages, temperature=0.2, max_tokens=512):
        return self._post({"model": self.model, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens})

    # ---- 视觉: 图片列表 + 问题（OpenAI image_url 格式, 兼容 GPT-4o/Qwen-VL/GLM-4V/Ollama vision） ----
    def chat_vision(self, images_b64: list, question: str, temperature=0.0, max_tokens=256):
        content = [{"type": "text", "text": question}]
        for b64 in images_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + b64}})
        return self.chat([{"role": "user", "content": content}], temperature, max_tokens)

    @staticmethod
    def b64(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
