# -*- coding: utf-8 -*-
from autosp.platform.base import AbstractTuningPlatform
from autosp.platform.offline_sim import OfflineSimPlatform

REGISTRY = {}

def register(name):
    def _w(cls):
        REGISTRY[name] = cls
        return cls
    return _w

def get_platform(name, **kw) -> AbstractTuningPlatform:
    """按名字取平台适配器。新增平台: 实现 AbstractTuningPlatform 并在这里注册。"""
    if name == "offline_sim":
        return OfflineSimPlatform(**kw)
    from autosp.platform.txw import TXW828Platform
    from autosp.platform.hailo import Hailo15Platform
    table = {"txw828": TXW828Platform, "hailo15h": Hailo15Platform}
    if name not in table:
        raise KeyError("未知平台 %s，可选: %s" % (name, list(table) + ["offline_sim"]))
    return table[name](**kw)
