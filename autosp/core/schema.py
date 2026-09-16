# -*- coding: utf-8 -*-
"""参数数据结构：ParamSpec / ParamSchema —— 平台参数的统一中间表示。

设计约定:
- 参数用扁平键 "module.name" 表示，如 "nlm.h"、"eeh.edge_gain"、"ae.target_luma"
- bv_stages 非空表示该参数按增益/亮度分档（一族参数），如夜间红外按增益分档降噪
- 换平台 = 换一份 schema JSON + 一个 platform adapter，框架其余部分不感知平台
"""
from dataclasses import dataclass, field
from typing import Optional
import json, os


@dataclass
class ParamSpec:
    key: str                      # "module.name"
    ptype: str = "int"            # int / float / enum / bool
    lo: float = 0.0
    hi: float = 1.0
    default: object = None
    bv_stages: list = field(default_factory=list)  # 分档档位列表，如 ["day","low_light","ir"]
    enum_values: Optional[list] = None
    unit: str = ""                # 单位/量纲说明
    desc: str = ""                # 作用描述（来自调参指南，LLM planner 的知识源）
    effects: str = ""             # 调大/调小对画面的影响，如 "增大→更平滑但细节损失"
    cmd: str = ""                 # 平台协议命令名（如 ISP_IOCTL_CMD_AE_LUMA_TARGET）
    arg_idx: int = 0              # 命令 payload 中的参数下标

    def to_dict(self):
        return self.__dict__.copy()

    def sample_default(self):
        return self.default if self.default is not None else self.lo

    def clamp(self, v):
        return max(self.lo, min(self.hi, v))


class ParamSchema:
    """一组参数的集合，负责加载/校验/搜索空间导出。"""

    def __init__(self, params: list):
        self.params = {p.key: p for p in params}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return cls([ParamSpec(**item) for item in raw["params"]])

    def keys(self, module=None):
        if module is None:
            return list(self.params)
        return [k for k in self.params if k.split(".")[0] == module]

    def modules(self):
        return sorted({k.split(".")[0] for k in self.params})

    def get(self, key):
        return self.params[key]

    def defaults(self, module=None):
        return {k: p.sample_default() for k, p in self.params.items()
                if module is None or k.split(".")[0] == module}

    def validate(self, values: dict):
        """合法性校验（越界/类型/未知参数），返回 (ok, msg)。调参安全第一道闸。"""
        for k, v in values.items():
            if k not in self.params:
                return False, "未知参数: %s" % k
            p = self.params[k]
            if p.ptype == "enum" and v not in (p.enum_values or []):
                return False, "%s 取值 %r 不在枚举 %s" % (k, v, p.enum_values)
            if p.ptype == "bool" and v not in (0, 1, True, False):
                return False, "%s 需要 bool(0/1), 得到 %r" % (k, v)
            if p.ptype in ("int", "float"):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    return False, "%s 需要数值, 得到 %r" % (k, v)
                if not (p.lo <= v <= p.hi):
                    return False, "%s=%r 越界 [%s, %s]" % (k, v, p.lo, p.hi)
        return True, ""
