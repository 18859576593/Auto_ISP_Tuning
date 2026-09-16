# -*- coding: utf-8 -*-
"""调参会话主循环（L4 runner）：planner 选模块 -> 优化器搜参数 -> 平台执行 -> 评价 -> 记录。

安全机制: 每轮写参前快照；指标异常(如黑图 objective 爆炸)自动回滚上一快照并跳过该候选。
"""
import os, time, json
from autosp.core.snapshot import SnapshotManager
from autosp.core.runlog import RunLog
from autosp.search.optimizer import make_optimizer


class TuningSession:
    def __init__(self, platform, objective, planner, optimizer_name="coord",
                 run_dir=None, vlm_judge=None):
        self.pf = platform
        self.objective = objective          # ObjectiveCombiner
        self.planner = planner
        self.opt_name = optimizer_name
        self.stop_flag = False              # 协作式停止（webapp/CLI 可置位）
        base = run_dir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data", "runs")
        self.run_dir = os.path.join(base, "%s_%s" % (platform.name, time.strftime("%m%d_%H%M%S")))
        os.makedirs(self.run_dir, exist_ok=True)
        self.snaps = SnapshotManager(self.run_dir)
        self.log = RunLog(self.run_dir)
        self.vlm = vlm_judge

    def _evaluate(self, params, module):
        self.snaps.save(self.pf._current, tag="pre")
        self.pf.set_params(params)
        captures = self.pf.capture("chart")
        res = self.objective(captures)
        rec = {"module": module, "params": {k: params[k] for k in
                                            (params if isinstance(params, dict) else [])},
               "captures": captures, **res}
        # 黑图/崩坏保护: objective 极端差时回滚
        if res["objective"] > self._guard(res):
            self.snaps.rollback(self.pf, self.snaps.list()[-2] if len(self.snaps.list()) > 1 else None)
        return rec

    def _guard(self, res):
        hist = [r for r in self.log.tail(999)]
        if not hist:
            return float("inf")
        return min(r["objective"] for r in hist) * 10 + 1e6

    def run(self, rounds=3, iters_per_round=6, verbose=True):
        schema = self.pf.get_schema()
        history = []
        for rd in range(rounds):
            if self.stop_flag:
                print("[session] 收到停止请求，提前结束")
                break
            # 每轮从历史最优出发，避免上一轮的失败候选污染基线
            if self.log.best:
                self.pf.set_params(self.log.best[1]["params"])
            plan = self.planner.next_plan(schema, history, self.pf.describe())
            keys = plan.params or schema.keys(plan.module)
            specs = {k: schema.get(k) for k in keys}
            opt = make_optimizer(self.opt_name, specs)
            for i in range(plan.iterations if plan.iterations else iters_per_round):
                if self.stop_flag:
                    break
                cand = opt.suggest(history)
                rec = self._evaluate(dict(self.pf._current, **cand), plan.module)
                opt.update(rec["params"], rec["objective"])
                self.log.add(rec)
                history.append(rec)
                if verbose:
                    print("  [%s #%02d] obj=%.4f %s %s" % (
                        plan.module, i + 1, rec["objective"],
                        {k: round(v, 3) for k, v in rec["metrics"].items()}, cand))
            if verbose:
                print("== 轮次 %d 完成: %s -> best obj=%.4f" % (rd + 1, plan, opt.best[0]))
        self.finish()

    def finish(self):
        best = self.log.best
        if best is None:
            print("无有效轮次")
            return
        obj, rec = best
        out = os.path.join(self.run_dir, "best_params.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rec["params"], f, ensure_ascii=False, indent=2)
        profile = os.path.join(self.run_dir, "profile_export.%s" %
                               ("yaml" if self.pf.name == "offline_sim" else "patch"))
        self.pf.export_profile(rec["params"], profile)
        print("\n最优 objective=%.4f\n参数: %s\n固化: %s / %s" % (obj, rec["params"], out, profile))
