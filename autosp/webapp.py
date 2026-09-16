# -*- coding: utf-8 -*-
"""autosp Web 工具：双击 启动autosp.bat 即可使用（浏览器界面，零额外依赖）。

API:
  GET  /                     界面
  GET  /api/state            平台/schema/LLM配置状态
  POST /api/tune/start       {platform, optimizer, rounds, iters}
  GET  /api/tune/status      运行状态+历史+最优
  POST /api/tune/stop
  POST /api/manual           {platform, params:{...}} 设参+抓图
  POST /api/rollback         {name?} 回滚最新/指定快照
  POST /api/vlm              {a,b,question} A/B对比(需LLM配置,否则mock)
  GET  /api/file?p=          采图预览(白名单: data/ refs/)
"""
import os, json, threading, glob, time, webbrowser, sys

# ROOT 解析：PyInstaller 打包后用 exe 所在目录（exe 与 platforms/refs/data 同级摆放）
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _HERE = os.path.dirname(os.path.abspath(__file__))     # .../autosp
    # 关键：以文件方式运行时脚本目录会进 sys.path，其中的 platform/ 子包会遮蔽标准库
    # platform 模块(numpy 内部 import platform)，先摘除再注入项目根
    for p in list(sys.path):
        if os.path.abspath(p or ".") == _HERE:
            sys.path.remove(p)
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from autosp.platform import get_platform
from autosp.eval.objectives import ObjectiveCombiner
from autosp.agent.guide_kb import GuideKB
from autosp.agent.planner import RulePlanner, LLMPlanner
from autosp.agent.runner import TuningSession
from autosp.agent.llm_client import llm_configured
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

STATE = {"session": None, "platform": None, "pf": None, "thread": None,
         "error": "", "started_at": ""}
PLATFORMS = {"offline_sim": "离线仿真(可跑闭环)", "txw828": "泰芯微TXW828(协议就绪待上板)",
             "hailo15h": "Hailo-15H(骨架)"}


def make_session(platform_name, optimizer, rounds, iters):
    pf = get_platform(platform_name)
    if platform_name == "offline_sim":
        objective = ObjectiveCombiner(pf.ref_image, {"psnr": -1.0, "ssim": -2.0})
    else:
        objective = ObjectiveCombiner("", {})      # dry-run 平台: 无指标, 仅走流程
    kb_path = os.path.join(ROOT, "platforms", platform_name, "guide.json")
    kb = GuideKB(kb_path) if os.path.exists(kb_path) else GuideKB.__new__(GuideKB)
    if not os.path.exists(kb_path):
        kb.g = {"tuning_order": pf.get_schema().modules(), "modules": {}}
    planner = LLMPlanner(kb) if llm_configured() else RulePlanner(kb)
    s = TuningSession(pf, objective, planner, optimizer_name=optimizer)
    return s, pf


def tune_worker(name, optimizer, rounds, iters):
    try:
        s, pf = make_session(name, optimizer, rounds, iters)
        STATE.update(session=s, platform=name, pf=pf, error="")
        s.run(rounds=rounds, iters_per_round=iters)
    except Exception as e:
        STATE["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        STATE["thread"] = None


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        out = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _file(self, path, mime):
        out = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._file(os.path.join(ROOT, "autosp", "web", "index.html"), "text/html; charset=utf-8")
        if u.path == "/api/state":
            out = {"platforms": PLATFORMS, "llm": llm_configured(), "running": STATE["thread"] is not None}
            for name in PLATFORMS:
                try:
                    pf = get_platform(name)
                    sch = pf.get_schema()
                    out.setdefault("schema", {})[name] = {
                        "modules": sch.modules(),
                        "params": [dict(p.to_dict()) for p in sch.params.values()]}
                except Exception as e:
                    out.setdefault("schema", {})[name] = {"error": str(e)}
            return self._json(out)
        if u.path == "/api/tune/status":
            s = STATE["session"]
            if s is None:
                return self._json({"running": False, "error": STATE["error"] or "尚未启动"})
            best = None
            if s.log.best:
                obj, rec = s.log.best
                best = {"objective": obj, "params": rec["params"], "metrics": rec["metrics"],
                        "captures": rec.get("captures", {})}
            return self._json({
                "running": STATE["thread"] is not None, "platform": STATE["platform"],
                "run_dir": s.run_dir, "best": best, "error": STATE["error"],
                "tail": s.log.tail(8)})
        if u.path == "/api/file":
            q = parse_qs(u.query).get("p", [""])[0]
            norm = os.path.abspath(q)
            if not (norm.startswith(os.path.join(ROOT, "data")) or norm.startswith(os.path.join(ROOT, "refs"))):
                return self._json({"error": "路径不在白名单"}, 403)
            mime = "image/png" if norm.endswith(".png") else "image/jpeg"
            return self._file(norm, mime)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/tune/start":
            if STATE["thread"] is not None:
                return self._json({"error": "已有任务在跑"}, 409)
            name = body.get("platform", "offline_sim")
            t = threading.Thread(target=tune_worker, args=(
                name, body.get("optimizer", "coord"),
                int(body.get("rounds", 2)), int(body.get("iters", 5))), daemon=True)
            STATE["thread"] = t
            STATE["started_at"] = time.strftime("%F %T")
            t.start()
            return self._json({"ok": True})
        if u.path == "/api/tune/stop":
            if STATE["session"]:
                STATE["session"].stop_flag = True
            return self._json({"ok": True})
        if u.path == "/api/manual":
            name = body.get("platform", "offline_sim")
            try:
                pf = STATE["pf"] if (STATE["pf"] and STATE["platform"] == name) else get_platform(name)
                pf.set_params(body.get("params", {}))
                caps = pf.capture("manual") or {}
                return self._json({"ok": True, "captures": caps})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if u.path == "/api/rollback":
            s = STATE["session"]
            if not s:
                return self._json({"error": "无会话"}, 400)
            params = s.snaps.rollback(s.pf, body.get("name"))
            return self._json({"ok": True, "params": params})
        if u.path == "/api/vlm":
            from autosp.eval.vlm_judge import VLMJudge
            backend = "openai" if llm_configured() else "mock"
            try:
                v = VLMJudge(backend=backend).compare(body["a"], body["b"],
                                                      body.get("question", "哪张整体画质更好?"))
                v["backend"] = backend
                return self._json(v)
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        return self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main(port=8765):
    url = "http://127.0.0.1:%d" % port
    print("autosp Web 工具启动: %s  (Ctrl+C 退出)" % url)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
