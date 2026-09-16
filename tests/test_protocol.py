# -*- coding: utf-8 -*-
"""TXW828 调参协议层单元测试（无需真实设备，串口用 FakeSerial 模拟）。

覆盖: CRC16-MODBUS / 命令帧构造 / ACK 解析 / 数据回传分包重组 / 命令表完整性
协议依据: docs/txw_tuning_protocol.md（SDK 逆向）
"""
import os
import struct

import pytest

from autosp.platform.txw import TunningProtocol, crc16_modbus

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------- CRC16-MODBUS ----------------

class TestCRC16:
    def test_standard_vector(self):
        # CRC-16/MODBUS 标准校验向量
        assert crc16_modbus(b"123456789") == 0x4B37

    def test_empty_returns_init(self):
        assert crc16_modbus(b"") == 0xFFFF

    def test_incremental_equivalence(self):
        data = bytes(range(256))
        assert crc16_modbus(data) == crc16_modbus(data[100:], crc16_modbus(data[:100]))


# ---------------- 命令帧构造 ----------------

class TestBuildCmd:
    def test_frame_layout(self):
        proto = TunningProtocol()
        frame = proto.build_cmd("ISP_IOCTL_CMD_AE_LUMA_TARGET", [110])
        head, cmd, ch, n = struct.unpack("<HHHH", frame[:8])
        payload = struct.unpack("<I", frame[8:12])[0]
        crc = struct.unpack("<H", frame[12:14])[0]
        assert head == 0xB103
        assert cmd == 55
        assert (ch, n) == (0, 1)
        assert payload == 110
        assert crc == crc16_modbus(frame[:12])
        assert len(frame) == 14

    def test_multi_args_with_channel(self):
        proto = TunningProtocol()
        frame = proto.build_cmd("ISP_IOCTL_CMD_AWB_CONTROL_STEP", [1, 2, 3], channel=2)
        head, cmd, ch, n = struct.unpack("<HHHH", frame[:8])
        args = struct.unpack("<III", frame[8:20])
        assert (n, ch) == (3, 2)
        assert args == (1, 2, 3)
        assert crc16_modbus(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]
        assert len(frame) == 8 + 12 + 2

    def test_no_args_frame(self):
        proto = TunningProtocol()
        frame = proto.build_cmd("ISP_IOCTL_CMD_GET_STA")
        head, cmd, ch, n = struct.unpack("<HHHH", frame[:8])
        assert (head, cmd, n) == (0xB103, 0, 0)
        assert len(frame) == 10

    def test_unknown_command_raises(self):
        proto = TunningProtocol()
        with pytest.raises(KeyError):
            proto.build_cmd("NOT_A_REAL_CMD")


# ---------------- 命令表完整性（137 条, 由 isp.h 枚举生成） ----------------

class TestCmdTable:
    def test_table_has_137_commands(self):
        assert len(TunningProtocol().cmd) == 137

    @pytest.mark.parametrize("name,num", [
        ("ISP_IOCTL_CMD_GET_STA", 0),
        ("ISP_IOCTL_CMD_GET_IMG", 18),
        ("ISP_IOCTL_CMD_AE_LUMA_TARGET", 55),
        ("ISP_IOCTL_CMD_SHARP_PARAM", 89),
        ("ISP_IOCTL_CMD_YUVNR_MAP", 91),
        ("ISP_IOCTL_CMD_GET_SENSOR_RAW", 103),
        ("ISP_IOCTL_CMD_CONFIG_SRAM_PARAM", 106),
        ("ISP_IOCTL_CMD_FUNC_ENABLE", 107),
    ])
    def test_known_command_numbers(self, name, num):
        assert TunningProtocol().cmd[name] == num

    def test_cmd_ids_unique_and_in_range(self):
        ids = TunningProtocol().cmd.values()
        assert len(set(ids)) == len(ids)
        assert min(ids) == 0 and max(ids) == 136


# ---------------- 串口模拟与响应构造 ----------------

class FakeSerial:
    """模拟 pyserial：write 记录发出的帧，read 依次消耗预置的响应字节。"""

    def __init__(self):
        self.rx = bytearray()
        self.tx = bytearray()

    def write(self, data):
        self.tx += data
        return len(data)

    def read(self, n):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def feed(self, data):
        self.rx += data

    def reset_input_buffer(self):
        # 真实串口会清掉未读字节; 测试里响应在 write 之后才到达, 置空实现
        # (flush-before-write 行为已在真机联调中验证)
        pass


def make_ack(cmd_num, ret=0, bad_crc=False):
    """构造 12 字节 ACK: [0xB103, cmd, ret, crc(前6字节), 0x0000, 0x000A]。"""
    body = struct.pack("<HHH", 0xB103, cmd_num, ret)
    crc = (crc16_modbus(body) + 1) & 0xFFFF if bad_crc else crc16_modbus(body)
    return body + struct.pack("<H", crc) + struct.pack("<HH", 0, 0x0A)


def make_data_response(cmd_num, data, bad_head_crc=False):
    """构造数据回传: 16 字节包头 + 每 2048 字节数据跟 4 字节尾部(实机实测分帧)。"""
    data_crc = crc16_modbus(data)   # 与实机可能不同; read_data 对 data_crc 仅告警
    hdr10 = struct.pack("<HHHHH", 0xB103, cmd_num, len(data) & 0xFFFF,
                        len(data) >> 16, data_crc)
    head_crc = (crc16_modbus(hdr10) + 1) & 0xFFFF if bad_head_crc else crc16_modbus(hdr10)
    out = bytearray(hdr10 + struct.pack("<H", head_crc) + struct.pack("<HH", 0, 0x0A))
    for i in range(0, len(data), 2048):
        out += data[i:i + 2048] + b"\xde\xad\xbe\xef"   # 4 字节尾部(语义未定)
    return bytes(out)


def make_proto():
    p = TunningProtocol()
    p.ser = FakeSerial()
    return p


# ---------------- ACK 解析 ----------------

class TestReadAck:
    def test_ok_ack_returns_zero(self):
        p = make_proto()
        p.ser.feed(make_ack(55, ret=0))
        assert p.read_ack("x") == 0

    def test_error_code_passthrough(self):
        p = make_proto()
        p.ser.feed(make_ack(55, ret=2))
        assert p.read_ack("x") == 2

    def test_bad_crc_raises(self):
        p = make_proto()
        p.ser.feed(make_ack(55, bad_crc=True))
        with pytest.raises(IOError, match="ACK"):
            p.read_ack("x")

    def test_bad_head_raises(self):
        p = make_proto()
        body = struct.pack("<HHH", 0xDEAD, 55, 0)
        p.ser.feed(body + struct.pack("<H", crc16_modbus(body)) + b"\x00" * 4)
        with pytest.raises(IOError, match="帧非法"):
            p.read_ack("x")

    def test_no_response_times_out(self):
        p = make_proto()
        with pytest.raises(TimeoutError):
            p.read_ack("x")


# ---------------- 数据回传（GET_IMG / RAW 等） ----------------

class TestReadData:
    def test_small_payload(self):
        p = make_proto()
        payload = b"JPEGDATA123"
        p.ser.feed(make_data_response(18, payload))
        assert p.read_data() == payload

    def test_empty_payload(self):
        p = make_proto()
        p.ser.feed(make_data_response(18, b""))
        assert p.read_data() == b""

    def test_multipacket_payload(self):
        p = make_proto()
        payload = os.urandom(5000)  # > 2048，需要 3 个分包
        p.ser.feed(make_data_response(18, payload))
        assert p.read_data() == payload

    def test_exact_packet_boundary(self):
        p = make_proto()
        size = 2048 * 2  # 恰好两个整包
        payload = os.urandom(size)
        p.ser.feed(make_data_response(103, payload))
        assert p.read_data() == payload

    def test_bad_head_crc_raises(self):
        p = make_proto()
        p.ser.feed(make_data_response(18, b"data", bad_head_crc=True))
        with pytest.raises(IOError, match="包头"):
            p.read_data()


# ---------------- 高层命令 ----------------

class TestHighLevelCommands:
    def test_set_cmd_roundtrip(self):
        p = make_proto()
        p.ser.feed(make_ack(55, 0))
        assert p.set_cmd("ISP_IOCTL_CMD_AE_LUMA_TARGET", [110]) == 0
        sent = bytes(p.ser.tx)
        head, cmd, ch, n = struct.unpack("<HHHH", sent[:8])
        assert (head, cmd, n) == (0xB103, 55, 1)
        assert struct.unpack("<I", sent[8:12])[0] == 110

    def test_get_img_roundtrip(self, tmp_path):
        p = make_proto()
        jpg = b"\xff\xd8" + os.urandom(64) + b"\xff\xd9"
        p.ser.feed(make_data_response(18, jpg))
        out = tmp_path / "cap.jpg"
        assert p.get_img(str(out)) == jpg
        assert out.read_bytes() == jpg

    def test_get_sensor_raw_roundtrip(self, tmp_path):
        p = make_proto()
        raw = os.urandom(4096)
        p.ser.feed(make_data_response(103, raw))
        out = tmp_path / "raw.bin"
        p.get_sensor_raw(str(out))
        assert out.read_bytes() == raw

    def test_get_version_roundtrip(self):
        p = make_proto()
        ver = b"TXW828 ISP v2.7.1.7"
        p.ser.feed(make_data_response(1, ver))
        assert p.get_version() == ver
