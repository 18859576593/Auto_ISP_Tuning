# -*- coding: utf-8 -*-
"""TXW828 平台适配层与参数 schema 的单元测试（dry-run，无需设备）。"""
import json
import struct

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


# ---------------- 多参数命令合帧（避免部分下发清零其余槽位） ----------------

class TestMultiArgGroupPacking:
    def _frames_of(self, pf):
        """切出已发送的帧: [(cmd_num, arg_count, args_bytes)]"""
        out, buf = [], bytes(pf.proto.ser.tx)
        i = 0
        while i + 12 <= len(buf):
            head_len = 8 + int.from_bytes(buf[i + 6:i + 8], "little") * 4
            frame = buf[i:i + head_len + 2]
            import struct as _s
            _, cmd, _, n = _s.unpack("<HHHH", frame[:8])
            out.append((cmd, n, frame[8:8 + n * 4]))
            i += head_len + 2
        return out

    def test_partial_update_fills_sibling_slots(self):
        """AE_DAY_NIGHT_BV 是双参数命令(day_bv,night_bv): 只改 day 也必须发满 2 参数。
        该命令线上为 IEEE754 位型(结构体 float 字段)。"""
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(45, 0))
        pf.set_params({"ae.day_night_bv": 9000.0})
        (cmd, n, args), = self._frames_of(pf)
        assert cmd == 45 and n == 2
        day, night = struct.unpack("<ff", args)
        night_default = pf.get_schema().get("ae.day_night_bv_night_bv").default
        assert day == 9000.0
        assert night == float(night_default)

    def test_same_cmd_params_merged_into_one_frame(self):
        """同一命令的两个参数只产生一帧。"""
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(45, 0))
        pf.set_params({"ae.day_night_bv": 8000.0, "ae.day_night_bv_night_bv": 2000})
        frames = self._frames_of(pf)
        assert len(frames) == 1
        assert struct.unpack("<ff", frames[0][2]) == (8000.0, 2000.0)

    def test_float_param_packed_as_ieee754_bits(self):
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(115, 0))
        pf.set_params({"sys.fps_opt": 25.5})
        (cmd, n, args), = self._frames_of(pf)
        assert cmd == 115 and n == 1
        assert struct.unpack("<f", args[:4])[0] == 25.5

    def test_current_value_used_after_two_updates(self):
        """第二次只改 night 时, day 用上次下发的值而非 default。"""
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(45, 0))
        pf.proto.ser.feed(make_ack(45, 0))
        pf.set_params({"ae.day_night_bv": 7000.0})
        pf.set_params({"ae.day_night_bv_night_bv": 3000})
        f2 = self._frames_of(pf)[-1]
        assert struct.unpack("<ff", f2[2]) == (7000.0, 3000.0)

    def test_int_param_packed_as_plain_int(self):
        """整型参数(如 AWB_CONTROL_STEP)直接整数值下发, 不做位型转换。"""
        pf = TXW828Platform(dry_run=False)
        pf.proto.ser = FakeSerial()
        pf.proto.ser.feed(make_ack(22, 0))   # AWB_CONTROL_STEP
        pf.set_params({"awb.control_step_fine": 5})
        (cmd, n, args), = self._frames_of(pf)
        assert cmd == 22 and n == 2
        coarse_dflt = pf.get_schema().get("awb.control_step_coarse").default
        assert struct.unpack("<II", args) == (int(coarse_dflt), 5)


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
