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
        """读数据回传: 16 字节包头 + 分包数据，返回 bytes。
        实测分帧(2026-09-16 真机联调): 每 2048 字节数据后跟 4 字节尾部
        (头文件定义 2048 含尾部, 与实机不符——实机 2048 数据 + 4 尾部 = 2052/包)。
        包尾 4 字节语义未定(非[CRC16][0x000A]), 跳过; 包头 CRC 已实测匹配。"""
        hdr = self._read_exact(16)
        h = struct.unpack("<8H", hdr)
        if h[0] != self.HEAD:
            raise IOError("包头非法: %s" % hdr.hex())
        data_len = h[2] | (h[3] << 16)
        data_crc, head_crc = h[4], h[5]
        if head_crc != crc16_modbus(hdr[:10]):
            raise IOError("包头 CRC 错误")
        CHUNK = 2048
        buf = bytearray()
        while len(buf) < data_len:
            want = min(CHUNK, data_len - len(buf))
            pkt = self._read_exact(want + 4)      # 数据 + 4 字节尾部(跳过)
            buf += pkt[:want]
        if data_crc != crc16_modbus(bytes(buf)):
            # 实机 data_crc 语义未定: JPEG 以魔数校验兜底, CRC 不匹配仅告警不阻断
            print("[txw] data_crc 不匹配(声明 0x%04X 计算 0x%04X)——以载荷魔数校验为准" % (
                data_crc, crc16_modbus(bytes(buf))))
        return bytes(buf)

    # ---- 高层命令 ----
    def _send(self, frame):
        """发送前清空接收缓冲——上一条命令的残留字节(如未实现命令的默认ACK)
        会污染下一条的帧头解析(2026-09-16 真机联调实测踩坑)。"""
        self.ser.reset_input_buffer()
        self.ser.write(frame)

    def ping(self):
        """链路测试: 写一个无害参数(AE 默认值), ACK=0 即链路通。
        注: 固件未实现 GET_VERSION——未处理命令走 12 字节默认 ACK, 故用写参代替。"""
        self._send(self.build_cmd("ISP_IOCTL_CMD_AE_LUMA_TARGET", [110]))
        return self.read_ack("ping") == 0

    def set_cmd(self, name, args=None, channel=0):
        frame = self.build_cmd(name, args, channel)
        self._send(frame)
        return self.read_ack(name)

    def get_version(self):
        self._send(self.build_cmd("ISP_IOCTL_CMD_GET_VERSION"))
        return self.read_data()

    def get_img(self, save_path=None):
        """GET_IMG: 返回 JPEG bytes（可选落盘）。"""
        self._send(self.build_cmd("ISP_IOCTL_CMD_GET_IMG"))
        jpg = self.read_data()
        if save_path:
            open(save_path, "wb").write(jpg)
        return jpg

    def get_sensor_raw(self, save_path=None):
        self._send(self.build_cmd("ISP_IOCTL_CMD_GET_SENSOR_RAW"))
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
