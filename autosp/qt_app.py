# -*- coding: utf-8 -*-
"""autosp Qt 桌面调参工作台：串口连接 + UVC 实时预览 + 参数快速应用。

启动: python autosp/qt_app.py 或 双击 启动autosp-Qt.bat
定位: TXW828 专用快速调参面板(Web 工作台保留全部多平台功能)
"""
import os
import sys
import time

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _p in list(sys.path):
        if os.path.abspath(_p or ".") == _HERE:
            sys.path.remove(_p)
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2
import re
import time
import subprocess
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QComboBox,
    QPushButton, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
    QTextEdit, QGroupBox, QHeaderView, QAbstractItemView,
)

from autosp.platform.txw import TXW828Platform

GREEN, ACCENT, MUTED = "#2F7D5B", "#D98E2B", "#8FA396"


def list_cameras():
    """枚举摄像头: [(显示名, cv2索引)]。
    用 ffmpeg -list_devices 拿 DirectShow 设备名(顺序即 cv2 索引)。
    兼容新旧 ffmpeg 输出格式; 失败则探测索引 0..4 兜底。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true",
             "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, timeout=15)
        out = r.stderr
        # 新版 ffmpeg: [in#0 @ ...] "NAME" (video)；旧版: 分节头 + "NAME"
        names = re.findall(r'"([^"]+)"\s+\(video\)', out)
        if not names and "DirectShow video devices" in out:
            vsec = out.split("DirectShow video devices", 1)[1] \
                       .split("DirectShow audio devices", 1)[0]
            names = re.findall(r'"([^"]+)"', vsec)[::2]
        if names:
            return [(n, i) for i, n in enumerate(names)]
    except Exception:
        pass
    cams = []
    for i in range(5):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            cams.append(("Camera %d" % i, i))
        cap.release()
    return cams

STYLE = """
QMainWindow, QWidget { background: #16261f; color: #e8efe9;
  font-family: "Microsoft YaHei"; font-size: 13px; }
QGroupBox { border: 1px solid #2e4a3e; border-radius: 8px; margin-top: 14px; padding: 8px 8px 8px 8px; }
QGroupBox::title { subcontrol: origin; left: 10px; padding: 0 4px; color: #D98E2B; font-weight: bold; }
QPushButton { background: #D98E2B; color: #1c2b26; font-weight: bold; border: none;
  border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #e8a44b; }
QPushButton:disabled { background: #3a4a42; color: #77857e; }
QComboBox, QLineEdit, QTextEdit, QTableWidget {
  background: #121e18; border: 1px solid #2e4a3e; border-radius: 6px; padding: 4px 8px; }
QHeaderView::section { background: #1d3129; color: #8fa396; border: none; padding: 5px; }
QTableWidget { gridline-color: #2e4a3e; }
QLabel#hint { color: #8fa396; font-size: 12px; }
QLabel#video { background: #0d1712; border: 1px solid #2e4a3e; border-radius: 6px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("autosp 调参工作台 · TXW828")
        self.resize(1400, 880)
        self.pf = None            # TXW 平台实例(连接后)
        self.cap = None           # UVC 摄像头
        self._defaults = {}       # 参数表初始值(用于识别改动)
        self._build_ui()
        self._timer = QTimer(self, interval=66, timeout=self._on_frame)          # UVC
        self._stream_timer = QTimer(self, interval=250, timeout=self._on_stream) # 串口连拍
        self._stream_n = 0
        self._stream_t0 = 0.0
        self.refresh_ports()
        self.refresh_cameras()

    # ================= UI =================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ---- 左列 ----
        left = QVBoxLayout()
        left.setSpacing(10)

        gb_conn = QGroupBox("设备连接")
        v1 = QVBoxLayout(gb_conn)
        row1 = QHBoxLayout()
        self.combo_port = QComboBox()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_ports)
        row1.addWidget(self.combo_port, 1)
        row1.addWidget(btn_refresh)
        row2 = QHBoxLayout()
        self.btn_connect = QPushButton("🔗 连接 (链路测试)")
        self.btn_connect.clicked.connect(self.connect_board)
        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.clicked.connect(self.disconnect_board)
        row2.addWidget(self.btn_connect, 1)
        row2.addWidget(self.btn_disconnect)
        self.lbl_conn = QLabel("未连接")
        self.lbl_conn.setObjectName("hint")
        v1.addLayout(row1)
        v1.addLayout(row2)
        v1.addWidget(self.lbl_conn)

        gb_prev = QGroupBox("实时预览 (UVC) + 串口抓图")
        v2 = QVBoxLayout(gb_prev)
        row3 = QHBoxLayout()
        self.combo_cam = QComboBox()
        self.combo_cam.setMinimumWidth(220)
        btn_cam_refresh = QPushButton("刷新")
        btn_cam_refresh.clicked.connect(self.refresh_cameras)
        self.btn_prev = QPushButton("▶ 预览")
        self.btn_prev.clicked.connect(self.preview_start)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.clicked.connect(self.preview_stop)
        self.btn_capture = QPushButton("📷 GET_IMG 抓图")
        self.btn_capture.clicked.connect(self.capture_still)
        self.btn_stream = QPushButton("🔄 串口连拍 (~6fps)")
        self.btn_stream.clicked.connect(self.stream_toggle)
        self.btn_stream.setToolTip("走调参协议连续抓图, 显示 ISP 处理后真实画面(与 GET_IMG 同源)。"
                                   "调参固件的 UVC 通路不喂流(实测全黑), 板子画面以此为准。")
        row3.addWidget(self.combo_cam, 1)
        row3.addWidget(btn_cam_refresh)
        row3.addWidget(self.btn_prev)
        row3.addWidget(self.btn_stop)
        row3.addWidget(self.btn_capture)
        row3.addWidget(self.btn_stream)
        self.lbl_video = QLabel("预览区")
        self.lbl_video.setObjectName("video")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setMinimumSize(640, 380)
        v2.addLayout(row3)
        v2.addWidget(self.lbl_video, 1)

        left.addWidget(gb_conn)
        left.addWidget(gb_prev, 1)

        # ---- 右列 ----
        right = QVBoxLayout()
        right.setSpacing(10)

        gb_params = QGroupBox("参数快速应用 (改动后点应用, 预览实时看效果)")
        v3 = QVBoxLayout(gb_params)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["参数", "值", "范围", "说明"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 100)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        btn_apply = QPushButton("⚡ 应用修改")
        btn_apply.clicked.connect(self.apply_params)
        v3.addWidget(self.table, 1)
        v3.addWidget(btn_apply)
        self.load_params()

        gb_log = QGroupBox("日志")
        v4 = QVBoxLayout(gb_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(180)
        v4.addWidget(self.txt_log)

        right.addWidget(gb_params, 1)
        right.addWidget(gb_log)

        root.addLayout(left, 7)
        root.addLayout(right, 5)

    # ================= 日志 =================
    def log(self, msg):
        self.txt_log.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))

    # ================= 连接 =================
    def refresh_ports(self):
        from serial.tools import list_ports
        self.combo_port.clear()
        ports = [p.device for p in list_ports.comports()]
        self.combo_port.addItems(ports if ports else ["(无串口)"])
        if ports:
            self.lbl_conn.setText("发现 %d 个串口" % len(ports))

    def _serial(self):
        p = self.combo_port.currentText()
        return None if p.startswith("(") else p

    def connect_board(self):
        port = self._serial()
        if not port:
            self.log("❌ 未选择串口")
            return
        try:
            if self.pf and self.pf.proto.ser:
                self.pf.proto.close()
            self.pf = TXW828Platform(port=port, dry_run=False)
            self.pf._ensure_link()
            t0 = time.time()
            ok = self.pf.proto.ping()
            dt = (time.time() - t0) * 1000
            if ok:
                self.lbl_conn.setText("✅ %s 已连接 (链路 %.0fms)" % (port, dt))
                self.log("✅ %s 连接成功, 链路测试 %.0fms" % (port, dt))
            else:
                self.lbl_conn.setText("⚠ %s ACK 异常" % port)
                self.log("⚠ %s ACK 异常" % port)
        except Exception as e:
            self.lbl_conn.setText("❌ 连接失败")
            self.log("❌ 连接失败: %s: %s" % (type(e).__name__, e))

    def disconnect_board(self):
        if self.pf and self.pf.proto.ser:
            self.pf.proto.close()
        self.pf = None
        self.lbl_conn.setText("已断开")
        self.log("串口已断开")

    # ================= 参数表 =================
    def load_params(self):
        sch = TXW828Platform().get_schema()
        self.table.setRowCount(len(sch.params))
        self._defaults = {}
        for r, spec in enumerate(sch.params.values()):
            d = spec.to_dict()
            self._defaults[d["key"]] = d.get("default")
            it0 = QTableWidgetItem(d["key"])
            it0.setFlags(it0.flags() & ~Qt.ItemIsEditable)
            val = str(d.get("default", ""))
            it1 = QTableWidgetItem(val)
            rng = "[%s, %s]" % (d.get("lo"), d.get("hi"))
            it2 = QTableWidgetItem(rng)
            it2.setFlags(it2.flags() & ~Qt.ItemIsEditable)
            it3 = QTableWidgetItem(d.get("desc", ""))
            it3.setFlags(it3.flags() & ~Qt.ItemIsEditable)
            for c, it in enumerate((it0, it1, it2, it3)):
                self.table.setItem(r, c, it)

    def _pf(self):
        if not (self.pf and self.pf.proto.ser):
            raise RuntimeError("请先连接板子")
        return self.pf

    def apply_params(self):
        try:
            pf = self._pf()
            changed = {}
            for r in range(self.table.rowCount()):
                key = self.table.item(r, 0).text()
                val_txt = self.table.item(r, 1).text().strip()
                if val_txt == "" or val_txt == str(self._defaults.get(key)):
                    continue
                val = float(val_txt)
                val = int(val) if val == int(val) else val
                changed[key] = val
            if not changed:
                self.log("没有参数改动(双击值列编辑)")
                return
            t0 = time.time()
            pf.set_params(changed)
            dt = (time.time() - t0) * 1000
            self.log("✅ 已应用 %s (%.0fms)" % (changed, dt))
            for k, v in changed.items():
                self._defaults[k] = v
        except Exception as e:
            self.log("❌ 应用失败: %s" % e)

    # ================= 预览/抓图 =================
    def refresh_cameras(self):
        """枚举并填充摄像头下拉框, 自动选中 RTT 板载摄像头(无则第一个)。"""
        was_running = self._timer.isActive()
        if was_running:
            self.preview_stop()
        cams = list_cameras()
        self.combo_cam.clear()
        for name, idx in cams:
            self.combo_cam.addItem(name, idx)
        # 自动选择: 优先 RTT(板载), 否则第一个
        sel = 0
        for i, (name, _idx) in enumerate(cams):
            if "RTT" in name.upper():
                sel = i
                break
        if cams:
            self.combo_cam.setCurrentIndex(sel)
            self.log("发现 %d 个摄像头, 已选中: %s" % (len(cams), cams[sel][0]))
        else:
            self.log("未发现摄像头")

    def preview_start(self):
        if self.combo_cam.count() == 0:
            self.log("❌ 无摄像头可选")
            return
        self.stream_stop()           # UVC 与串口连拍互斥
        idx = self.combo_cam.currentData()
        name = self.combo_cam.currentText()
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            self.cap = None
            self.log("❌ 打不开摄像头: %s" % name)
            return
        self._timer.start()
        self.log("▶ 预览已启动: %s (索引 %d)" % (name, idx))

    def preview_stop(self):
        self._timer.stop()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.lbl_video.setText("预览区")
        self.log("■ 预览已停止")

    # ================= 串口连拍预览 =================
    def stream_toggle(self):
        if self._stream_timer.isActive():
            self.stream_stop()
        else:
            self.stream_start()

    def stream_start(self):
        try:
            self._pf()   # 无连接则抛异常
        except Exception as e:
            self.log("❌ %s" % e)
            return
        self.preview_stop()          # UVC 与串口连拍互斥
        self._stream_n = 0
        self._stream_t0 = time.time()
        self.btn_stream.setText("⏸ 停止连拍")
        self._stream_timer.start()
        self.log("🔄 串口连拍已启动 (GET_IMG, ~6fps, ISP 处理后画面)")

    def stream_stop(self):
        self._stream_timer.stop()
        self.btn_stream.setText("🔄 串口连拍 (~6fps)")
        self.log("■ 串口连拍已停止")

    def _on_stream(self):
        try:
            pf = self._pf()
            jpg = pf.proto.get_img()
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            self.lbl_video.setPixmap(QPixmap.fromImage(qimg).scaled(
                self.lbl_video.width(), self.lbl_video.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._stream_n += 1
            if self._stream_n % 30 == 0:
                fps = self._stream_n / (time.time() - self._stream_t0)
                self.log("… 连拍 %d 帧, 平均 %.1f fps" % (self._stream_n, fps))
        except Exception as e:
            self.stream_stop()
            self.log("❌ 连拍中断: %s" % e)

    def _on_frame(self):
        if self.cap is None:
            return
        ret, frame = self.cap.read()
        if not ret:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(
            self.lbl_video.width(), self.lbl_video.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.lbl_video.setPixmap(pix)

    def capture_still(self):
        """走串口 GET_IMG(与官方工具同路), 显示到预览区并落盘。"""
        try:
            pf = self._pf()
            t0 = time.time()
            jpg = pf.proto.get_img()
            dt = (time.time() - t0) * 1000
            import numpy as np
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                self.log("❌ GET_IMG %d 字节但解码失败" % len(jpg))
                return
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg).scaled(
                self.lbl_video.width(), self.lbl_video.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_video.setPixmap(pix)
            path = os.path.join("data", "runs", "txw828",
                                "cap_%s.jpg" % time.strftime("%H%M%S"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path, img)
            self.log("📷 GET_IMG: %dx%d, %d字节, %.0fms → %s (均值%.1f)" % (
                w, h, len(jpg), dt, path, img.mean()))
        except Exception as e:
            self.log("❌ GET_IMG 失败: %s" % e)

    # ================= 退出 =================
    def closeEvent(self, ev):
        self._timer.stop()
        self._stream_timer.stop()
        if self.cap is not None:
            self.cap.release()
        if self.pf and self.pf.proto.ser:
            self.pf.proto.close()
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
