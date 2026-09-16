# autosp —— 自动 ISP 调参框架

> 目标：把"调参指南 + 客观指标 + 优化搜索 + LLM/VLM 编排"组合成可复用的自动调参工具。
> 首个平台：泰芯微 TXW828（在线协议调参）；框架先用离线仿真平台验证（Phase 1 已跑通）。
> 调研依据：`D:\ISP IQ Tuning\isp tuning\泰芯微平台资料\自动ISP调优调研与方案.md`

## 一、架构（四层 ↔ 四阶段）

```
L4 agent/    编排层   guide_kb 指南知识库 + planner(规则版可用/LLM版留接口) + runner 会话主循环
L3 search/   搜索层   CoordinateDescent(内置) / RandomSearch(内置) / Optuna·OpenBox(可选后端)
L2 eval/     评价层   metrics: PSNR/SSIM(内置)·LPIPS(可选) + vlm_judge: A/B对比评审(可插拔后端)
L1 platform/ 适配层   base 抽象接口 + offline_sim(已跑通) + txw828/hailo15h(骨架)
```

**换平台 = 实现一个 adapter（set_params/capture/export_profile）+ 一份 param_schema.json + 一份 guide.json。** 框架其余部分零改动。

## 二、目录

```
autosp/            框架包（core/platform/eval/search/agent 五个子包）
platforms/         每平台一份: param_schema.json(参数+作用+影响描述) + guide.json(调参顺序+规则)
  txw828/            泰芯微（来自 SDK IQ 参数模型 + 笔记 §3.4）
  hailo15h/          Hailo-15H（来自 iq_settings/3aconfig 结构）
  offline_sim/       fast-OpenISP 仿真平台
scripts/demo_phase1.py   Phase1 演示入口
refs/              参考项目（浅克隆）: fast-OpenISP / ISP-AutoTuning / Infinite-ISP_TuningTool
data/runs/         运行产物: history.jsonl + snapshots/ + best_params.json + profile导出
```

## 三、四阶段路线与现状

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Phase 0** 参数/指南结构化 | param_schema.json + guide.json 格式已定，TXW/Hailo 示例已建；待 FAE 指南到位后补全字段 | ✅ 格式与示例 |
| **Phase 1** 离线闭环 | offline_sim 平台 + PSNR/SSIM 目标 + 坐标下降，**已跑通**：12 轮 120s，PSNR 32.67→33.56，SSIM 0.972→0.976，产出 best_params + 快照 + 历史 | ✅ 跑通 |
| **Phase 2** 实机闭环 | TXW828 适配器骨架（USB 协议 TODO 清单在 txw.py 文件头）；Hailo 骨架（离线型平台，两段式） | 🚧 骨架 |
| **Phase 3** LLM 编排 | **LLM 已接入**（OpenAI 兼容客户端，mock 服务端到端验证通过）：LLMPlanner 决策+失败自动回退规则版；VLMJudge A/B 对比 | ✅ 已接入(待真实 API) |

## 三点五、大模型接入（Phase 3）

一个客户端通吃云端与本地（OpenAI 兼容格式），配置三选一即可启用：

```bash
# 方式A: 环境变量
export AUTOSP_LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"  # 智谱;或 http://localhost:11434/v1 (Ollama)
export AUTOSP_LLM_API_KEY="你的key"      # 本地服务填任意非空
export AUTOSP_LLM_MODEL="glm-4v-flash"   # 需视觉版模型(如 glm-4v/qwen-vl), planner 可用纯文本模型

# 方式B: autosp/llm_config.json  {"base_url":..., "api_key":..., "model":...}
```

接入点与用途：
- `agent/llm_client.py`：统一客户端（chat + chat_vision 图片对比）
- `agent/planner.py LLMPlanner`：读指南+指标历史 → 决定下一模块与方向（JSON 输出，解析失败自动回退 RulePlanner，闭环永不中断）
- `eval/vlm_judge.py backend="openai"`：A/B 对比评审（当前帧 vs 上一轮 vs 标杆锚点），主观场景（红外）主力

设计原则不变：**LLM 做策略决策、VLM 做对比评审；数值搜索始终是优化器**——大模型挂了框架照跑。

## 四、快速开始

### 方式一：双击 Web 工具（推荐）

- **双击 `autosp-webapp.exe`**（或 `启动autosp.bat`）→ 自动打开浏览器 `http://127.0.0.1:8765`
- 界面功能：选平台/优化器 → 开始自动调参（实时看迭代历史/最优参数/采图预览）→ 手动设参抓图 → 一键回滚快照 → VLM A/B 对比评审
- exe 需与 `platforms/ refs/ data/` 目录**同级**摆放（打包时已就位）；关掉黑窗口即退出

### 方式二：命令行

```bash
cd D:\auto-isp-tuning
python scripts/demo_phase1.py        # Phase1 离线闭环演示（约 2 分钟）
```

### 迭代更新（改代码后）

- 改 `autosp/` 下 Python 或 `web/index.html` → 用 bat 启动的**刷新浏览器即生效**；exe 启动的需双击 `打包exe.bat` 重新打包（约 1 分钟）
- 新增平台：`autosp/platform/xxx.py` 实现三接口 + `platform/xxx/param_schema.json` + guide.json → 注册进 `platform/__init__.py`
- LLM 配置：环境变量 `AUTOSP_LLM_BASE_URL/_API_KEY/_MODEL`（详见"三点五"节）

### 运行测试（协议层回归网，改协议代码后必跑）

```bash
python -m pytest tests/ -q
```

46 个用例覆盖：CRC16-MODBUS 标准向量、命令帧构造（帧头/参数数/CRC）、ACK 解析（正常/错误码/坏CRC/坏帧头/超时）、数据回传分包重组（空载荷/单包/多包/整包边界/坏CRC）、命令表完整性（137条+关键命令号）、参数合法性闸门（越界/未知/类型/枚举）、平台层 FakeSerial 全链路。

### 克隆后首次部署

```bash
git clone https://github.com/18859576593/Auto_ISP_Tuning.git
cd Auto_ISP_Tuning
pip install -r requirements.txt        # numpy/opencv/yaml
pip install pyserial pytest            # 实机联调与测试
# refs/ 参考仓库不入库，按需克隆（见第七节）
python scripts/demo_phase1.py          # 验证离线闭环
```

代码级使用：

```python
from autosp.platform import get_platform
from autosp.eval.objectives import ObjectiveCombiner
from autosp.agent.guide_kb import GuideKB
from autosp.agent.planner import RulePlanner
from autosp.agent.runner import TuningSession

pf = get_platform("offline_sim")                       # 换平台只改这里
objective = ObjectiveCombiner(pf.ref_image, {"psnr": -1.0, "ssim": -2.0})
planner = RulePlanner(GuideKB("platforms/offline_sim/guide.json"))
TuningSession(pf, objective, planner, optimizer_name="coord").run(rounds=2, iters_per_round=5)
```

## 五、安全机制

- **schema.validate**：越界/类型/未知参数第一道闸（set_params 强制走）
- **SnapshotManager**：每轮写参前落快照，`rollback(platform)` 一键回滚
- **黑图保护**：objective 比历史最优差 10 倍+1e6 时自动回滚（runner._guard）
- **每轮从历史最优出发**：失败候选不污染下一轮基线

## 六、下一步（按优先级）

1. **TXW 实机**（Phase 2）：等 FAE 上位机或按 `sdk/include/hal/isp_tunning.h` 自研 USB 会话 → 填实 txw.py 的 4 个 TODO；先做 AE 目标亮度单参数闭环
2. **指南全量结构化**（Phase 0）：TXW 调参指南到位后把全部参数补进 param_schema.json（含 BV 分档展开）
3. **VLM 接入**（Phase 3）：vlm_judge.py 的 http 后端接多模态服务，先做"本轮 vs 上轮"复核；红外主观场景用
4. **LPIPS**：Python 环境降到 3.10/3.11 后启用 torch，感知指标比 PSNR 更贴近人眼

## 七、参考项目（refs/，克隆于 2026-09-01）

| 项目 | 借鉴点 |
|---|---|
| coolsyn2000/ISP-AutoTuning | 最小闭环样板：OpenBox + LPIPS + fast-OpenISP（本框架 Phase1 的对标） |
| QiuJueqin/fast-openISP | Python ISP 管线（offline_sim 后端） |
| 10x-Engineers/Infinite-ISP_TuningTool | 色卡标定工具（BLC/WB/CCM/噪声估计自动化，Phase2 标定环节可借） |
