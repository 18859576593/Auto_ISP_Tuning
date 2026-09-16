# -*- coding: utf-8 -*-
"""泰芯微 TXW828 适配器（Phase 2：真实协议实现，待上板联调）。

协议: docs/txw_tuning_protocol.md（SDK 逆向: CDC 串口 + 0xB103 帧头 + CRC16-MODBUS）
命令数值表: platforms/txw828/cmd_table.json（137 条, 由 sdk/include/hal/isp.h 枚举生成）

联调三步（拿到断 VBUS 的 USB A-A 线后）:
  1) proto = TunningProtocol(); proto.open("COMx")
  2) proto.get_version()                    # 链路通
  3) proto.set_cmd("ISP_IOCTL_CMD_AE_LUMA_TARGET", [110]) → ACK
     proto.get_img() -> JPEG bytes          # 第一个闭环就绪
"""
import os, json, struct, time

from autosp.platform.base import AbstractTuningPlatform


# ---------------- CRC16-MODBUS（与固件 hw_crc CRC_TYPE_CRC16_MODBUS 一致） ----------------
def crc16_modbus(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class TunningProtocol:
    """TXW ISP 调参协议客户端（CDC 串口）。"""
    HEAD = 0xB103
    ACK_OK = 0x55AA
    ACK_ERR = 0x5A5A
    PACKET = 2048

    def __init__(self, cmd_table_path=None):
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "platforms", "txw828")
        with open(cmd_table_path or os.path.join(base, "cmd_table.json"), encoding="utf-8") as f:
            self.cmd = json.load(f)
        self.ser = None

    # ---- 传输 ----
    def open(self, port, baud=115200, timeout=3.0):
        import serial  # pip install pyserial
        self.ser = serial.Serial(port, baud, timeout=timeout)
        return self.ser.is_open

    def close(self):
        if self.ser:
            self.ser.close()

    # ---- 帧构造 ----
    def build_cmd(self, name: str, args=None, channel: int = 0) -> bytes:
        """args: u32 列表（浮点命令先 struct.pack 成 u32 位型）。"""
        args = args or []
        head = struct.pack("<HHHH", self.HEAD, self.cmd[name], channel, len(args))
        payload = b"".join(struct.pack("<I", a & 0xFFFFFFFF) for a in args)
        body = head + payload
        return body + struct.pack("<H", crc16_modbus(body))

    # ---- 收包 ----
    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if not chunk:
                raise TimeoutError("串口读超时(已收%d/%d)" % (len(buf), n))
            buf += chunk
        return buf

    def read_ack(self, cmd_name: str):
        """读 12 字节 ACK: [head, cmd, ret, crc, 0, 0x0A]，校验 CRC。"""
        raw = self._read_exact(12)
        head, cmd, ret, crc = struct.unpack("<HHHH", raw[:8])
        if head != self.HEAD or crc != crc16_modbus(raw[:6]):
            raise IOError("ACK 帧非法: %s" % raw.hex())
        return ret

    def read_data(self):
        """读数据回传: 16B 包头 + 分包数据，返回 bytes。"""
        hdr = self._read_exact(16)
        h = struct.unpack("<8H", hdr)
        if h[0] != self.HEAD:
            raise IOError("包头非法: %s" % hdr.hex())
        data_len = h[2] | (h[3] << 16)
        data_crc, head_crc = h[4], h[5]
        if head_crc != crc16_modbus(hdr[:10]):
            raise IOError("包头 CRC 错误")
        buf = b""
        while len(buf) < data_len:
            pkt = self._read_exact(min(self.PACKET, data_len - len(buf) + 4))
            body, pcrc, _lb = pkt[:-4], struct.unpack("<H", pkt[-4:-2])[0], pkt[-2:]
            if pcrc != crc16_modbus(hdr[:0] + body) and pcrc != crc16_modbus(body):
                pass  # 分包CRC: 联调时按实测修正(可能含包头前缀), 先容错
            buf += body
        if data_crc != crc16_modbus(buf):
            raise IOError("数据 CRC 错误")
        return buf

    # ---- 高层命令 ----
    def set_cmd(self, name, args=None, channel=0):
        frame = self.build_cmd(name, args, channel)
        self.ser.write(frame)
        return self.read_ack(name)

    def get_version(self):
        self.ser.write(self.build_cmd("ISP_IOCTL_CMD_GET_VERSION"))
        return self.read_data()

    def get_img(self, save_path=None):
        """GET_IMG: 返回 JPEG bytes（可选落盘）。"""
        self.ser.write(self.build_cmd("ISP_IOCTL_CMD_GET_IMG"))
        jpg = self.read_data()
        if save_path:
            open(save_path, "wb").write(jpg)
        return jpg

    def get_sensor_raw(self, save_path=None):
        self.ser.write(self.build_cmd("ISP_IOCTL_CMD_GET_SENSOR_RAW"))
        raw = self.read_data()
        if save_path:
            open(save_path, "wb").write(raw)
        return raw


class TXW828Platform(AbstractTuningPlatform):
    name = "txw828"
    online = True

    def __init__(self, port=None, dry_run=True, workdir=None):
        self.schema_path = os.path.join(self.params_dir, "txw828", "param_schema.json")
        self.dry_run = dry_run
        self.port = port
        self.proto = TunningProtocol()
        self._current = {}
        self.workdir = workdir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data", "runs", "txw828")
        os.makedirs(self.workdir, exist_ok=True)

    def _ensure_link(self):
        if self.dry_run:
            raise RuntimeError("dry_run=True 不连设备；实机联调时传 dry_run=False + port='COMx'")
        if self.proto.ser is None:
            self.proto.open(self.port)

    # schema 键 -> (命令名, 参数索引, 缩放) 的映射在 param_schema.json 的 "cmd" 字段
    def set_params(self, params: dict):
        ok, msg = self.get_schema().validate(params)
        if not ok:
            raise ValueError(msg)
        self._current.update(params)
        if self.dry_run:
            print("[TXW828 dry-run] set %s" % params)
            return
        self._ensure_link()
        schema = self.get_schema()
        for key, val in params.items():
            spec = schema.get(key)
            cmd_name = getattr(spec, "cmd", None) or spec.to_dict().get("cmd")
            idx = spec.to_dict().get("arg_idx", 0) if spec else 0
            # 单参数命令: 该参数独立发一帧（简化模型; 多参数组合命令在联调时扩展）
            ret = self.proto.set_cmd(cmd_name, [int(val)] if float(val).is_integer() else [val])
            if ret != 0:
                raise IOError("命令 %s(%s) 返回错误码 %s" % (key, cmd_name, ret))

    def capture(self, scene="default") -> dict:
        if self.dry_run:
            print("[TXW828 dry-run] capture scene=%s" % scene)
            return {}
        self._ensure_link()
        path = os.path.join(self.workdir, "cap_%s_%d.jpg" % (scene, int(time.time())))
        self.proto.get_img(path)
        return {scene: path}

    def capture_raw(self, path=None):
        """RAW 抓取（BLC/LSC 标定自动化用，固件需 DUAL_EN=1）。"""
        if self.dry_run:
            print("[TXW828 dry-run] capture_raw")
            return None
        self._ensure_link()
        path = path or os.path.join(self.workdir, "raw_%d.bin" % int(time.time()))
        self.proto.get_sensor_raw(path)
        return path

    def export_profile(self, params: dict, out_path: str):
        """实机固化走工具 ExportSingleParam→isp.bin 或 CONFIG_SRAM_PARAM；此处导出记录。"""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("// TXW828 autosp 参数固化记录（实机: ExportSingleParam→isp.bin 或 CONFIG_SRAM_PARAM 在线写）\n")
            json.dump(params, f, ensure_ascii=False, indent=2)
        return out_path
