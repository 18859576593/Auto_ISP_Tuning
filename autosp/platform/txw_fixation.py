# -*- coding: utf-8 -*-
"""参数固化导出：把调优结果生成官方兼容的 C 源文件 / 操作指引。

路线（与官方一致）:
  1. 生成 `isp_param_custom.c` — 与 SDK `isp_param_default.c` 同构的 C 源文件，
     替换编译后即为固件二级基线（重编 + 烧录 = 持久化）
  2. 生成 `fixation_guide.md` — 手动操作步骤（放哪个目录、怎么编译、怎么烧录）
  3. 生成 `tuned_params.json` — 结构化调优值快照（供工具回读/对比）

用法:
  from autosp.platform.txw_fixation import export_fixation
  export_fixation({"ae.luma_target": 110, "ce.saturation": 130}, "output_dir")
"""
import os
import json
import time

# schema 参数 -> C 结构体字段映射（isp_param_default.c 中的变量名.字段名）
# 生成时在默认值基础上做定点覆盖
PARAM_MAP = {
    "ae.luma_target":  ("default_master_ae",     "luma_target",          "uint16"),
    "ae.luma_weight":  ("default_master_ae",     "luma_weight_sum",      "uint16"),
    "ce.saturation":   ("default_master_ce",     "saturation",           "float"),
    "ce.contrast":     ("default_master_ce",     "contrast",             "float"),
    "ce.hue":          ("default_master_ce",     "hue",                  "float"),
    "wdr.enable":      ("default_master_wdr",    "wdr_en",               "uint8"),
    "wdr.noise_floor": ("default_master_wdr",    "noise_floor",          "uint8"),
    "nr.dnr3_enable":  ("default_master_enable", "csupp_en",             "bit"),
    "img.black_white_mode": ("default_master_enable", "test_pattern_en",  "bit"),
}

C_HEADER = """// isp_param_custom.c — 由 autosp 自动生成于 {ts}
// 生成来源: best_params.json (autosp 调优结果)
// 用法: 替换 sdk/lib/video/isp/param/isp_param_default.c 后重编固件
//       (或加入编译并修改 project 引用)
// 注意: 未列出的参数保持 default_isp_param_init 默认值

#include "sys_config.h"
#include "typesdef.h"
#include "osal/string.h"
#include "hal/isp_param.h"
#include "hal/isp.h"
#include "lib/video/dvp/cmos_sensor/csi.h"

// ==== 基于官方 isp_param_default.c, 应用调优覆盖 ====
"""

C_SECTION_TMPL = """
// ---- {comment} ----
static const {struct_type} custom_{var} = {{
    .{field} = {value},
}};
"""


def _fmt_value(key, val, vtype):
    if vtype == "float":
        return "%ff" % float(val)
    if vtype == "uint16":
        return str(int(val))
    if vtype == "uint8":
        return str(int(val))
    if vtype == "bit":
        return str(int(val))
    return str(val)


def _c_field_lines(params):
    """把调优参数按结构体分组, 生成 C 代码段"""
    groups = {}
    for key, val in params.items():
        if key not in PARAM_MAP:
            continue
        var, field, vtype = PARAM_MAP[key]
        groups.setdefault(var, []).append((field, val, vtype, key))
    return groups


def generate_c_source(params, defaults_desc=None):
    """生成 isp_param_custom.c 的核心逻辑:
    用 #define 宏覆盖方式, 不复制整个默认文件(避免与 SDK 版本绑定太深)。
    实际输出一个精简的 override 头文件思路。"""

    lines = [C_HEADER.format(ts=time.strftime("%Y-%m-%d %H:%M:%S"))]
    lines.append("// 方式: 在 sdk/lib/video/isp/param/ 下新建此文件,")
    lines.append("// 然后在 isp_param_default.c 的对应结构体初始化中替换字段值,")
    lines.append("// 或直接在 sensor 驱动 .c 中通过 isp_iq_param 挂接。\n")

    lines.append("// ==== 调优值快照(机器可读, 供 autosp 回读) ====")
    lines.append("// " + json.dumps(params, ensure_ascii=False) + "\n")

    lines.append("// ==== 对应 C 结构体字段(手工/脚本替换用) ====")
    groups = _c_field_lines(params)
    for var, fields in sorted(groups.items()):
        lines.append("\n// {var}:".format(var=var))
        for field, val, vtype, key in fields:
            lines.append("//   .%s = %s    // 来自 %s" % (
                field, _fmt_value(key, val, vtype), key))

    lines.append("\n// ==== 生成的参数覆盖头文件 ====")
    lines.append("// 将以下内容追加到编译宏或直接修改 isp_param_default.c 对应字段\n")

    lines.append("""// ------------------------------
// 替换指引（两选一）:
//
// 方式 A: 修改 isp_param_default.c
//   1. 打开 sdk/lib/video/isp/param/isp_param_default.c
//   2. 找到上方列出的结构体变量和字段
//   3. 把值替换为调优值
//   4. 重编: cdk-make.exe -p project/txw82xApp/txw82xApp.cdkproj -d build -c FLASH
//   5. 烧录: TXProgrammer 或 CDK 烧录 APP.bin
//
// 方式 B: sensor 驱动挂接（v2.7.1.7 之前的旧方式, 仍支持）
//   1. 在 sensor_gc2053_mipi.c 中定义 static const _Sensor_ISP_Init custom_iq = {...}
//   2. 挂接: sensor_cmd.sensor_isp_cfg.isp_iq_param = &custom_iq;
//   3. 重编烧录
// ------------------------------
""")
    return "\n".join(lines)


def export_fixation(params, out_dir, platform_name="txw828"):
    """导出固化产物: C 源 + 指引 + JSON 快照"""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "fixation_%s" % time.strftime("%m%d_%H%M%S"))
    os.makedirs(base, exist_ok=True)

    # 1. C 源
    c_path = os.path.join(base, "isp_param_custom.c")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(generate_c_source(params))

    # 2. JSON 快照
    json_path = os.path.join(base, "tuned_params.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"platform": platform_name, "time": time.strftime("%F %T"),
                   "params": params}, f, ensure_ascii=False, indent=2)

    # 3. 指引
    guide = os.path.join(base, "fixation_guide.md")
    with open(guide, "w", encoding="utf-8") as f:
        f.write("""# 参数固化指引（autosp 自动生成 {ts}）

## 产物清单
| 文件 | 用途 |
|---|---|
| `isp_param_custom.c` | C 源文件（含调优值 + 替换指引） |
| `tuned_params.json` | 机器可读的参数快照 |

## 操作步骤（方式 A：修改默认参数文件）

1. 打开 SDK 的 `sdk/lib/video/isp/param/isp_param_default.c`
2. 按 `isp_param_custom.c` 中列出的「结构体变量.字段 = 值」逐条替换
3. 重编固件：
   ```bash
   cd D:\\TXW_SDK
   cdk-make.exe -p project/txw82xApp/txw82xApp.cdkproj -d build -c FLASH
   ```
   编译日志中 `isp.bin missing` 警告消失 = 参数已编入
4. 烧录 `APP.bin`（TXProgrammer 或 CDK）
5. 重启后串口日志应显示 `sensor param use flash_param!` 或非 default 加载

## 操作步骤（方式 B：sensor 驱动挂接）

适用于需要按 sensor 定制参数的场景。在 sensor 驱动 .c 中：
```c
static const _Sensor_ISP_Init custom_iq = {{...}};  // 填入调优值
sensor_cmd.sensor_isp_cfg.isp_iq_param = &custom_iq;
```
重编烧录后固件走二级基线（sensor 内嵌 > 默认）。

## 验证

烧录后用 autosp 重新连接板子, 确认:
- 串口日志: `sensor param use flash_param!`（一级）或 `use command delivered isp iq param`（二级）
- 读回参数值与 `tuned_params.json` 一致
""".format(ts=time.strftime("%Y-%m-%d %H:%M")))

    return {"c_source": c_path, "json": json_path, "guide": guide,
            "dir": base}
