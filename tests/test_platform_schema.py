# -*- coding: utf-8 -*-
"""TXW828 平台适配层与参数 schema 的单元测试（dry-run，无需设备）。"""
import json

import pytest

from autosp.platform.txw import TXW828Platform
from autosp.platform.hailo import Hailo15Platform

from test_protocol import FakeSerial, make_ack


# ---------------- 平台层（dry-run） ----------------

class TestTXW828PlatformDryRun:
    def test_set_params_updates_current(self, capsys):
        pf = TXW828Platform(dry_run=True)
        pf.set_params({"ae.luma_target": 100, "nr.dnr3_enable": 1})
        assert pf._current == {"ae.luma_target": 100, "nr.dnr3_enable": 1}
        assert "dry-run" in capsys.readouterr().out

    def test_capture_returns_empty_in_dry_run(self):
        pf = TXW828Platform(dry_run=True)
        assert pf.capture("chart") == {}

    def test_capture_raw_returns_none_in_dry_run(self):
        pf = TXW828Platform(dry_run=True)
        assert pf.capture_raw() is None

    def test_export_profile_writes_json(self, tmp_path):
        pf = TXW828Platform(dry_run=True)
        out = tmp_path / "profile.patch"
        pf.export_profile({"ae.luma_target": 110}, str(out))
        content = out.read_text(encoding="utf-8")
        data = json.loads(content[content.index("{"):])
        assert data == {"ae.luma_target": 110}


# ---------------- 参数合法性闸门（dry-run 下也必须拦截） ----------------

class TestParamValidation:
    def test_out_of_range_rejected(self):
        pf = TXW828Platform(dry_run=True)
        with pytest.raises(ValueError, match="越界"):
            pf.set_params({"ae.luma_target": 999})

    def test_unknown_param_rejected(self):
        pf = TXW828Platform(dry_run=True)
        with pytest.raises(ValueError, match="未知参数"):
            pf.set_params({"no.such_param": 1})

    def test_bool_accepts_only_0_1(self):
        pf = TXW828Platform(dry_run=True)
        with pytest.raises(ValueError, match="bool"):
            pf.set_params({"wdr.enable": 2})

    def test_type_check_rejects_string(self):
        pf = TXW828Platform(dry_run=True)
        with pytest.raises(ValueError, match="数值"):
            pf.set_params({"ae.luma_target": "高"})


# ---------------- 真实协议路径（FakeSerial 走完整链路） ----------------

class TestPlatformOverFakeSerial:
    def test_set_params_sends_protocol_frames(self):
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        # 两条参数各需要一个 ACK
        pf.proto.ser.feed(make_ack(55, 0))   # ae.luma_target -> CMD 55
        pf.proto.ser.feed(make_ack(36, 0))   # nr.dnr3_enable -> 3DNR_ENABLE
        pf.set_params({"ae.luma_target": 110, "nr.dnr3_enable": 1})
        frames = bytes(pf.proto.ser.tx)
        assert frames.count(b"\x03\xb1") == 2  # 0xB103 小端出现在两帧头

    def test_set_params_raises_on_error_ack(self):
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(55, ret=1))  # CFG_PARAM_ERR
        with pytest.raises(IOError, match="错误码"):
            pf.set_params({"ae.luma_target": 110})


# ---------------- Schema 加载与校验 ----------------

class TestSchemas:
    def test_txw_schema_defaults_all_valid(self):
        sch = TXW828Platform(dry_run=True).get_schema()
        ok, msg = sch.validate(sch.defaults())
        assert ok, msg

    def test_txw_schema_params_have_cmd_mapping(self):
        sch = TXW828Platform(dry_run=True).get_schema()
        for spec in sch.params.values():
            assert spec.cmd, "标量参数 %s 缺少协议命令映射" % spec.key
            assert spec.cmd in TXW828Platform().proto.cmd

    def test_hailo_enum_validation(self):
        sch = Hailo15Platform(dry_run=True).get_schema()
        ok, _ = sch.validate({"denoise.generation": "gen3_hdm"})
        assert ok
        ok2, msg = sch.validate({"denoise.generation": "gen9"})
        assert not ok2

    def test_hailo_gain_float_boundary(self):
        sch = Hailo15Platform(dry_run=True).get_schema()
        assert sch.validate({"ae.maxSensorAgain": 64.0})[0]
        assert not sch.validate({"ae.maxSensorAgain": 65.0})[0]
