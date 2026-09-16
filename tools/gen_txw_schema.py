# -*- coding: utf-8 -*-
"""从 TXW SDK 源码自动生成全量调参 schema（对齐官方工具参数面）。

数据链(全部可复核):
  hal/isp.c          ISP_IOCTL_CMD 包装函数 → 命令参数表(名称/类型/顺序)
  isp_tunning.c      固件调参线程 switch → 哪些命令真正实现 + 线序修正 + 表格命令
  isp_param.h        结构体定义 → 字段注释(GBK 中文) → 参数描述
  isp_param_default.c 出厂默认值 → default 字段
  param_schema.json  既有手工条目按 (cmd, arg_idx) 优先保留(范围/默认/描述不覆盖)

用法: python tools/gen_txw_schema.py [--sdk D:\\TXW_SDK] [--dry]
输出: platforms/txw828/param_schema.json (params + table_params 全量重写)
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 常量映射
GROUPS = [  # (命令前缀, 模块名, 模块中文)
    ("ISP_IOCTL_CMD_AE_", "ae", "自动曝光"),
    ("ISP_IOCTL_CMD_HIST_", "ae", "曝光统计"),
    ("ISP_IOCTL_CMD_LOWLIGHT_", "ae", "低照增益"),
    ("ISP_IOCTL_CMD_AWB_", "awb", "自动白平衡"),
    ("ISP_IOCTL_CMD_CE_", "ce", "色彩增强"),
    ("ISP_IOCTL_CMD_WDR_", "wdr", "宽动态"),
    ("ISP_IOCTL_CMD_SHARP", "sharp", "锐化"),
    ("ISP_IOCTL_CMD_GIC", "gic", "绿紫修正"),
    ("ISP_IOCTL_CMD_LHS", "lhs", "局部色相饱和"),
    ("ISP_IOCTL_CMD_MD_", "md", "移动检测"),
    ("ISP_IOCTL_CMD_3DNR", "nr", "3D降噪"),
    ("ISP_IOCTL_CMD_DYN_YGAMMA", "gamma", "动态伽马"),
    ("ISP_IOCTL_CMD_FUNC_ENABLE", "sys", "模块开关"),
    ("ISP_IOCTL_CMD_FPS_OPT", "sys", "帧率"),
    ("ISP_IOCTL_CMD_IMG_", "img", "图像翻转"),
    ("ISP_IOCTL_CMD_CSC_", "csc", "色彩空间"),
    ("ISP_IOCTL_CMD_DPC_", "dpc", "坏点校正"),
]

TYPE_RANGE = {
    "uint8": (0, 255, "int"), "int8": (-128, 127, "int"),
    "uint16": (0, 65535, "int"), "int16": (-32768, 32767, "int"),
    "uint32": (0, 4294967295, "int"), "int32": (-2147483648, 2147483647, "int"),
    "float": (0.0, 1.0e9, "float"),
}

# 表格类命令(结构体指针下发) → 描述; word 数标 N/A 的走标定流程
TABLE_CMDS = {
    "ISP_IOCTL_CMD_AWB_GAIN_CONSTRAINT": ("awb.gain_limit", "AWB 增益上下限+粗精约束结构体(default/min/max 各4通道)"),
    "ISP_IOCTL_CMD_AWB_GAIN_COARSE_CONSTRAINT": ("awb.coarse_polygon", "AWB 粗约束多边形(coarse_min/lb/rt/max_bg/rg)"),
    "ISP_IOCTL_CMD_AWB_GAIN_FINE_CONSTRAINT": ("awb.fine_polygon", "AWB 细约束多边形(8 段色温线斜率/截距/角点)"),
    "ISP_IOCTL_CMD_AWB_WP_RANGE_CONSTRAINT_RESTRAIN": ("awb.wp_restrain", "AWB 白点 restrain 结构体"),
    "ISP_IOCTL_CMD_AE_SCENE_LUT": ("ae.scene_lut", "AE 场景查找表"),
    "ISP_IOCTL_CMD_AE_LOWLIGHT_PARAM": ("ae.lowlight", "AE 低照参数结构体"),
    "ISP_IOCTL_CMD_AE_CROP_RANGE": ("ae.crop", "AE 测光窗口(start_h/v + size_h/v)"),
    "ISP_IOCTL_CMD_HIST_CROP_RANGE": ("ae.hist_crop", "直方图统计窗口(start_h/v + end_h/v)"),
    "ISP_IOCTL_CMD_WDR_TUNNING": ("wdr.tunning", "WDR 调参结构体"),
    "ISP_IOCTL_CMD_GAMMA_BY_BV_PARAM": ("gamma.by_bv", "BV 分档伽马表(8 档)"),
    "ISP_IOCTL_CMD_CE_OFFSET_PARAM": ("ce.offset", "CE 色偏修正结构体"),
    "ISP_IOCTL_CMD_CE_ADJ_BY_BV_PARAM": ("ce.adj_by_bv", "CE 按 BV 分档参数表(8 档)"),
    "ISP_IOCTL_CMD_CCM_ARRAY": ("ccm.matrix", "3x3 CCM 矩阵(标定: 24色卡拍摄)"),
    "ISP_IOCTL_CMD_BLC_PARAM": ("blc.param", "BLC 四通道黑电平(标定: 遮黑盖+GET_SENSOR_RAW)"),
    "ISP_IOCTL_CMD_CSC_PARAM": ("csc.param", "CSC 色彩空间转换矩阵"),
    "ISP_IOCTL_CMD_DPC_PARAM": ("dpc.param", "DPC 坏点检测强度结构体"),
    "ISP_IOCTL_CMD_SHARP_PARAM": ("sharp.param", "锐化参数结构体"),
    "ISP_IOCTL_CMD_RAWNR_MAP": ("nr.rawnr_map", "BV 分档 RAW 域降噪强度表(11 档)"),
    "ISP_IOCTL_CMD_YUVNR_MAP": ("nr.yuvnr_map", "BV 分档 YUV 降噪强度表(6 档)"),
    "ISP_IOCTL_CMD_CSUPP_MAP": ("nr.csupp_map", "BV 分档色度抑制表(3 档)"),
    "ISP_IOCTL_CMD_Y_GAMMA_TUNNING": ("gamma.y_curve", "Y Gamma 曲线(64 点)"),
    "ISP_IOCTL_CMD_RGB_GAMMA_TUNNING": ("gamma.rgb_curve", "RGB Gamma 曲线"),
    "ISP_IOCTL_CMD_LSC_TUNNING": ("lsc.tbl", "LSC 阴影校正表(标定: 均匀光源)"),
    "ISP_IOCTL_CMD_GIC_PARAM": ("gic.param", "GIC 参数结构体(w_thres/w_slope/w_str/mu_*)"),
    "ISP_IOCTL_CMD_LHS_MAP": ("lhs.map", "局部色相/饱和 9 区映射"),
}

# 固件 switch 未实现 → 排除(发送会落 default 分支持 ACK 成功但无效果)
SKIP_CMDS = {
    "ISP_IOCTL_CMD_BLACK_WHITE_MODE", "ISP_IOCTL_CMD_FILTER_MODE",
    "ISP_IOCTL_CMD_AWB_MANNUL_MODE_MAP", "ISP_IOCTL_CMD_AE_BV",
    "ISP_IOCTL_CMD_AE_EV_OFFSET_LUT", "ISP_IOCTL_CMD_LUMA_CA_PARAM",
    "ISP_IOCTL_CMD_YUV_RANGE", "ISP_IOCTL_CMD_CE_ADJ_BY_BV",
    "ISP_IOCTL_CMD_SETTING_BAYER_PATTEN", "ISP_IOCTL_CMD_SET_FRAME_LEN",
    "ISP_IOCTL_CMD_CAMERA_MODE", "ISP_IOCTL_CMD_SET_ANTI_FLICKER",
}

# 线序修正表已不需要——parse_dispatch 按固件调用点对齐自动处理换序/channel 参数
WRAPPER_NAMES = {}


# 线上为 IEEE754 位型的命令(依据: 结构体字段为 float + 固件 FPS_OPT 的 *(float*)p_data 惯例)
FLOAT_CMDS = {
    "ISP_IOCTL_CMD_AE_ABL_BV_THR",       # abl_bv_thr[2] float
    "ISP_IOCTL_CMD_AE_AOE_BV_THR",       # aoe_bv_thr[2] float
    "ISP_IOCTL_CMD_AE_DAY_NIGHT_BV",     # 日夜切换 BV 阈值(官方预设 8000/10000 为浮点量纲)
}

# 手工字段映射: HAL 参数名 → isp_param_default.c 字段名(自动精确匹配失败时)
FIELD_MAP = {
    "coarse": "coarse_scale", "fine": "fine_step",
    "coarse_thr": "coarse_thr", "hi_thr": "lock_hi_thr", "lo_thr": "lock_lo_thr",
    "rgb_thr": "stable_thr", "yuv_thr": "cbcr_thr",
    "gain_type": "awb_gain_type", "meas_mode": "awb_meas_mode",
    "luma_target": "luma_target", "luma_weight_sum": "luma_weight_sum",
}

# 人工描述补充(生成 desc 优先级: 手工 > isp_param.h 注释 > 模块名·参数名)
DESC_OVERRIDES = {
    ("ISP_IOCTL_CMD_AE_LUMA_TARGET", 0): "AE 目标亮度(最终 luma target)",
    ("ISP_IOCTL_CMD_AWB_AUTO_CTRL", 0): "AWB 自动模式使能(0手动/1自动)",
    ("ISP_IOCTL_CMD_AWB_MEAS_MODE", 0): "AWB 统计模式(0:YUV,1:RGB,2:YUV_NEW)",
    ("ISP_IOCTL_CMD_3DNR_ENABLE", 0): "3D 降噪使能",
    ("ISP_IOCTL_CMD_FUNC_ENABLE", 0): "ISP 各子模块使能位掩码(blc/lsc/dpc/awb/ccm/gamma/sharp/...)，见 isp_param.h isp_func_cfg 位定义",
    ("ISP_IOCTL_CMD_FPS_OPT", 0): "帧率优化目标(fps, 浮点位型下发)",
    ("ISP_IOCTL_CMD_AE_MANUAL_PARAM", 0): "AE 手动使能(0自动/1手动)",
}


def read_gbk(path, gbk_first=False):
    encs = ("gbk", "utf-8") if gbk_first else ("utf-8", "gbk")
    for enc in encs:
        try:
            return open(path, encoding=enc).read()
        except UnicodeDecodeError:
            continue
    return open(path, encoding="gbk", errors="replace").read()


def _iter_functions(src):
    """按大括号配对切出每个函数 (签名, 函数体)。"""
    sig_pat = re.compile(r'(\w+)\s*\(\s*struct\s+isp_device\s*\*[^)]*\)\s*\{')
    for m in sig_pat.finditer(src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            if src[i] == '{':
                depth += 1
            elif src[i] == '}':
                depth -= 1
            i += 1
        # group(0) 形如 "NAME(params) {", 去掉末尾 '{' 后取参数括号段
        sig_full = m.group(0)[:-1]
        yield m.group(1), sig_full[sig_full.index('('):], src[m.end():i - 1]


def parse_wrappers(isp_c):
    """hal/isp.c → {cmd: [(argname, argtype), ...]}(线序)。
    每个函数只认自己体内 ioctl 调用的命令, 参数顺序以 param[] 初始化为准。"""
    out = {}
    for fname, sig, body in _iter_functions(isp_c):
        cm = re.search(r'ISP_IOCTL_CMD_(\w+)', body)
        if not cm:
            continue
        cmd = "ISP_IOCTL_CMD_" + cm.group(1)
        if cmd in out:
            continue
        # 签名参数列表
        psig = sig[sig.index('('):]
        plist = []
        for p in psig.split(','):
            p = p.strip()
            if not p or 'sensor_type' in p or p.startswith('struct') or '(' in p:
                continue
            tm = re.match(r'(?:const\s+)?(u?int\d+|float)\s+(\w+)', p)
            if tm:
                plist.append((tm.group(2), tm.group(1)))
        # 线序: param[] = {...} 中的名字顺序
        m = re.search(r'param\[[\w\s]*\]\s*(?:=\s*\{([^}]*)\}|)', body)
        if m and m.group(1):
            order = [x.strip() for x in m.group(1).split(',') if x.strip()]
            d = dict(plist)
            plist = [(n, d.get(n, "uint32")) for n in order if n in d]
        out[cmd] = plist
    return out


def _split_top_commas(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append(''.join(cur).strip())
    return parts


def parse_dispatch(tunning_c):
    """固件线程 switch → {cmd: {"slots": {wire_idx: (包装参数名,类型)}, "table_at": N|None}}
    线序以固件调用点为准: fn(p_dev->p_isp, <各参数>, p_dev->cmd_channel)。
    - p_data[N]  → 标量槽 N
    - (uint32)p_data / (cast)&p_data[N] → 表格(从 N 或 0 起)
    参数名/类型从 HAL 包装 param[] 按调用位置对齐取。"""
    wrappers = globals().setdefault("_wrappers_cache", None)
    cases = re.findall(
        r'case\s+(ISP_IOCTL_CMD_\w+)\s*:\s*(.*?)(?=\bcase\s+ISP_IOCTL|\bdefault:)', tunning_c, re.S)
    out = {}
    for name, body in cases:
        if name == "ISP_IOCTL_CMD_FPS_OPT":
            out[name] = {"slots": {0: ("fps", "float")}, "table_at": None}
            continue
        # 找 handler 调用: xxx(p_dev->p_isp, ..., p_dev->cmd_channel)
        m = re.search(r'\w+\s*\(\s*p_dev->p_isp\s*,(.*)p_dev->cmd_channel', body, re.S)
        if not m:
            out[name] = {"slots": {}, "table_at": 0}
            continue
        args = _split_top_commas(m.group(1))
        wrapper = WRAPPER_NAMES.get(name, [])
        slots, table_at = {}, None
        for d, a in enumerate(args):
            am = re.search(r'p_data\s*(?:\[(\d+)\])?', a)
            if not am:
                continue
            idx_s = am.group(1)
            is_table = ('&' in a or ('(' in a and idx_s is None)) and '&' in a or idx_s is None
            if idx_s is None:                       # (uint32)p_data 整体表格
                table_at = table_at if table_at is not None else 0
                continue
            i = int(idx_s)
            if '&' in a:                            # (uint32)&p_data[N] 表格起点
                table_at = i
                continue
            pname, ptype = (wrapper[d] if d < len(wrapper) else ("arg%d" % i, "uint32"))
            slots[i] = (pname, ptype)
        out[name] = {"slots": slots, "table_at": table_at}
    return out


def repair_mojibake(s):
    """厂商源码注释常见 GBK→latin1 错转: 尝试 latin1 重编码回 GBK 还原中文。"""
    if not s or not re.search(r'[·×£»¼]', s):
        return s
    try:
        fixed = s.encode("latin-1").decode("gbk")
        # 还原成功判据: 中文占比明显提高
        if len(re.findall(r'[\u4e00-\u9fff]', fixed)) > len(re.findall(r'[\u4e00-\u9fff]', s)):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return s


def parse_struct_comments(isp_param_h):
    """isp_param.h → {字段名: 中文注释}(跨结构体同名字段后者覆盖, AE/AWB 在前即可)"""
    comments = {}
    for line in isp_param_h.splitlines():
        m = re.search(r'[\s\*](\w+)\s*(?::\s*\d+\s*,?)?\s*;?\s*/\s*/\s*(\S.*)$', line)
        m = m or re.search(r'(\w+)\s*(?::\s*\d+\s*,?)?\s*;\s*//\s*(.+)$', line)
        if m and m.group(2).strip():
            comments[m.group(1)] = repair_mojibake(m.group(2).strip().rstrip('*/ ').strip())
    return comments


def parse_defaults(default_c):
    """isp_param_default.c → {字段名: 值}; 数组字段展开为 field[0]/field[1]/..."""
    vals = {}
    for m in re.finditer(r'\.(\w+)\s*=\s*(?:\(uint32\))?(\d+(?:\.\d+)?)', default_c):
        vals.setdefault(m.group(1), float(m.group(2)) if '.' in m.group(2) else int(m.group(2)))
    for m in re.finditer(r'\.(\w+)\s*=\s*\{([^}]*)\}', default_c):
        nums = re.findall(r'\d+(?:\.\d+)?', m.group(2))
        if len(nums) in (2, 3, 4):  # 小数组才展开(大表无意义)
            for i, n in enumerate(nums):
                vals.setdefault("%s[%d]" % (m.group(1), i),
                                float(n) if '.' in n else int(n))
    return vals


GROUP_PREFIX = {p: g for p, g, _ in GROUPS}


def cmd_stem(cmd):
    """AE_ABL_BV_THR → 'abl_bv_thr'(只剥模块引导词, 保留完整语义词干)"""
    stem = cmd[len("ISP_IOCTL_CMD_"):].lower()
    for lead in ("ae_", "awb_", "ce_", "wdr_", "hist_", "lowlight_", "md_", "img_"):
        if stem.startswith(lead):
            return stem[len(lead):]
    return stem


def group_of(cmd):
    for prefix, g, gname in GROUPS:
        if cmd.startswith(prefix):
            return g, gname
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdk", default=r"D:\TXW_SDK\sdk")
    ap.add_argument("--dry", action="store_true", help="只打印统计不写文件")
    args = ap.parse_args()

    isp_c = read_gbk(os.path.join(args.sdk, "hal", "isp.c"))
    tunning_c = read_gbk(os.path.join(args.sdk, "lib", "video", "isp", "isp_tunning.c"))
    param_h = read_gbk(os.path.join(args.sdk, "include", "hal", "isp_param.h"), gbk_first=True)
    default_c = read_gbk(os.path.join(args.sdk, "lib", "video", "isp", "param", "isp_param_default.c"))

    wrappers = parse_wrappers(isp_c)
    global WRAPPER_NAMES
    WRAPPER_NAMES = {c: [(n, t) for n, t in plist] for c, plist in wrappers.items()}
    dispatch = parse_dispatch(tunning_c)
    implemented = set(dispatch)
    comments = parse_struct_comments(param_h)
    defaults = parse_defaults(default_c)

    ov_path = os.path.join(ROOT, "platforms", "txw828", "param_overrides.json")
    old = json.load(open(ov_path, encoding="utf-8"))
    overrides = {(p.get("cmd"), p.get("arg_idx", 0)): p for p in old["params"]}
    old_tables = {c: {"cmd": c, **w} for c, w in old.get("table_words", {}).items()}

    scalars, stats = [], {"skip_noutil": 0, "skip_nofw": 0, "table": 0, "scalar": 0}
    seen_keys = {}
    for cmd in sorted(implemented, key=lambda c: (group_of(c)[0] or "zz", c)):
        g, gname = group_of(cmd)
        if g is None:
            continue
        if cmd in SKIP_CMDS:
            stats["skip_noutil"] += 1
            continue
        disp = dispatch[cmd]
        plist = sorted(disp["slots"].items())  # [(wire_idx, (name, type))]
        if not plist:
            stats["table"] += 1
            continue
        for idx, (pname, ptype_raw) in plist:
            ptype_raw = re.sub(r'\s*\*.*', '', ptype_raw)  # 去指针
            rng = TYPE_RANGE.get(ptype_raw)
            if rng is None:
                continue
            lo, hi, ptype = rng
            ov = overrides.get((cmd, idx))
            if ov:  # 既有手工条目: 范围/默认值/描述/影响保留, 只补齐
                scalars.append(ov)
                seen_keys[ov["key"]] = cmd
                continue
            stem = cmd_stem(cmd)
            # key: 单参数=组.词干; 多参数=组.词干_参数名(参数名泛化时用下标)
            if len(plist) == 1:
                key = "%s.%s" % (g, stem)
            else:
                tail = pname if not re.match(r'^(arg\d+|val\d+|data|buf)$', pname) else "p%d" % idx
                key = "%s.%s" % (g, stem) if tail == stem else "%s.%s_%s" % (g, stem, tail)
            if key in seen_keys:
                key = "%s_%d" % (key, idx)   # 仍冲突则加参数下标
            seen_keys[key] = cmd
            # 默认值候选: 数组展开字段(stem[i]) → 词干 → 手工映射 → 参数名
            cand = (["%s[%d]" % (stem, idx)] if len(plist) > 1 else []) \
                + [stem, FIELD_MAP.get(pname, pname)]
            dflt = next((defaults[c] for c in cand if c in defaults), None)
            if dflt is None:
                dflt = 0 if ptype == "int" else 0.0
            if cmd in FLOAT_CMDS:
                ptype, lo, hi = "float", 0.0, 1.0e9
                dflt = float(dflt)
            if isinstance(dflt, float) and ptype == "int":
                dflt = int(dflt)
            if ptype == "float":
                lo, hi = 0.0, max(1.0e9, float(dflt or 0) * 10)
            desc = DESC_OVERRIDES.get((cmd, idx)) or comments.get(stem) or comments.get(FIELD_MAP.get(pname, pname)) \
                or comments.get(pname) or "%s·%s" % (gname, key.split(".", 1)[1])
            scalars.append({
                "key": key, "ptype": ptype,
                "lo": lo, "hi": hi, "default": dflt,
                "cmd": cmd, "arg_idx": idx,
                "desc": desc[:80],
            })
            stats["scalar"] += 1

    # 表格命令条目
    tables = []
    for cmd, (key, desc) in sorted(TABLE_CMDS.items()):
        if cmd in SKIP_CMDS:
            continue
        t = old_tables.get(cmd, {})
        tables.append({
            "key": t.get("key", key), "cmd": cmd,
            "words": t.get("words"), "desc": t.get("desc", desc),
            **({"bv_arg": t["bv_arg"]} if "bv_arg" in t else {}),
        })

    print("HAL包装解析: %d 条命令" % len(wrappers))
    print("固件分发实现: %d 条" % len(implemented))
    print("标量参数(schema params): %d 条" % len(scalars))
    print("表格命令(table_params): %d 条" % len(tables))
    print("跳过: 固件未实现 %d, 无HAL包装 %d" % (stats["skip_noutil"], stats["skip_nofw"]))

    if args.dry:
        for p in scalars:
            print("  %-38s %-7s [%s, %s] dft=%s  %s" % (
                p["key"], p["ptype"], p["lo"], p["hi"], p["default"], p["desc"][:40]))
        return

    out = {
        "platform": "txw828",
        "note": "由 tools/gen_txw_schema.py 从 SDK(hal/isp.c + isp_tunning.c + isp_param*.h/c)自动生成; "
                "手工条目按 (cmd,arg_idx) 优先保留。重生成: python tools/gen_txw_schema.py",
        "table_params": tables,
        "params": scalars,
    }
    path = os.path.join(ROOT, "platforms", "txw828", "param_schema.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("已写入 %s" % path)


if __name__ == "__main__":
    sys.exit(main())
