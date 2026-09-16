# TXW828 ISP 在线调参协议逆向文档

> 来源：SDK v2.7.1.7 源码逆向（`sdk/include/hal/isp_tunning.h` + `sdk/lib/video/isp/isp_tunning.c` + `sdk/lib/bus/rttusb/usbdevice/class/cdc_vcom.c` + `sdk/include/hal/isp.h`）
> 用途：自研 Python 调参会话客户端（替代/复用 TXISP Tuning 上位机），供 autosp 平台适配器调用
> 日期：2026-09-04

## 1. 传输层

- **USB CDC 串口**（复合设备：CDC 串口 + UVC 摄像头）。上位机用 pyserial 打开对应 COM 口即可
- 备用通道：WinUSB（winusb.c 有同款解析），TXISP Tuning 实际走 CDC（exe 带 Qt5SerialPort）
- 固件侧前提：`CUSTOMER_ID=4`（isp_tuning_demo）+ `ISP_TUNNING_EN`

## 2. 主机→设备 命令帧（CDC bulk out，小端）

```
偏移      字段            说明
0x00 u16  head            固定 0xB103
0x02 u16  cmd_num         命令号（见 cmd_table.json，enum isp_ioctl_cmd 从 0 起）
0x04 u16  cmd_channel     通道/参数组索引
0x06 u16  cmd_data        参数个数 N（每个参数 4 字节 u32/f32）
0x08 u32×N payload        参数（小端；FPS_OPT 等个别命令是 float）
尾   u16  crc             CRC16-MODBUS，覆盖 0x00 ~ 0x08+4N-1 全部字节
```

固件校验：head==0xB103 且 CRC 匹配，否则丢弃（串口打印 head/crc 错误日志）。

## 3. 设备→主机 响应

**简单 ACK（12 字节 = u16×6）**：`[0xB103, cmd_num, ret_val, crc16(前6字节), 0x0000, 0x000A]`
- ret_val=0 成功；错误码见 `TUNNING_ERR_CODE_*`（GET/CFG PARAM ERR / MALLOC ERR）

**数据回传（GET_IMG / GET_SENSOR_RAW / GET_YUV_DATA / GET_*_GAMMA / GET_LSC / DUMP_*）**：
```
包头 16 字节: [0xB103 u16][cmd_num u16][data_len u32 拆两个u16][data_crc u16][head_crc u16][0x00 0x0A u32]
数据: 按 2KB 包续传，每包 = data(≤2048-4B) + package_crc u16 + line_break u16(0x000A)
data_crc = CRC16-MODBUS(整段数据)；head_crc = CRC16-MODBUS(包头前 10 字节)
```
- GET_IMG：JPEG（msi "auto-jpg"，TUNNING_IMG_JPEG；H264 模式另有分支）
- GET_SENSOR_RAW：原始 Bayer RAW（需固件 `DUAL_EN=1`，BLC/LSC 标定用）
- RW_SENSOR_REG：`payload=[reg_len, rw_mode, reg_addr(, data)]`，读回走数据回传（1 字节）

## 4. CRC16-MODBUS

poly=0xA001（反射），init=0xFFFF，无异或输出。自检向量：CRC16-MODBUS("123456789") = **0x4B37**。

## 5. 命令集（137 个，数值表见 platforms/txw828/cmd_table.json）

| 分组 | 代表命令 | payload 语义（来自 isp_tunning.c 分发） |
|---|---|---|
| 系统 | GET_STA(0) GET_VERSION(1) DUMP_PARAM(2) PROGRAM_PARAM(3) | 版本/参数 dump 与固化 |
| 模块开关 | FUNC_ENABLE(107) 3DNR_ENABLE WDR_EN BLACK_WHITE_MODE | 总使能位 |
| AE | AE_LUMA_TARGET(55) AE_LUMA_WEIGHT(56) AE_DAY_NIGHT_BV(45) AE_MANUAL_PARAM AE_AOE_* AE_ABL_* AE_STG_PARAM AE_EV_OFFSET_* | 目标亮度/权重/日夜BV阈值/防过曝AOE/强光抑制ABL |
| AWB | AWB_GAIN_TYPE/CONSTRAINT/COARSE/FINE AWB_WP_* AWB_MEAS_MODE AWB_CROP_RANGE | 白点约束/增益约束/测量模式 |
| 标定类 | BLC_PARAM LSC_TUNNING CCM_ARRAY(86) Y_GAMMA/RGB_GAMMA_TUNNING | 标定表写入（BLC/LSC/CCM/Gamma） |
| 降噪/锐化 | RAWNR_MAP(90) YUVNR_MAP(91) SHARP_PARAM(89) CSUPP_MAP | **按 BV 分档的 map 表**（p_data[0]=?, [1]=档数, 后跟表） |
| 色彩 | CE_LUMA/SATURATION/CONTRAST/HUE CE_ADJ_BY_BV_PARAM LHS_MAP CSC_PARAM GIC_PARAM | 色彩增强/LHS/CSC/绿平衡 |
| WDR/低照 | WDR_TUNNING WDR_NOISE_FLOOR GAMMA_BY_BV_PARAM/ENABLE(105/104) LOWLIGHT_LSB_GAIN_* AE_REDUCE_FPS | 宽动态/低照增益/按BV的gamma |
| 采图 | GET_IMG(18) GET_SENSOR_RAW(103) GET_YUV_DATA | JPEG/RAW/YUV 回传 |
| 其他 | MD_WINDOW/DETECT_PARAM(运动检测) AE_SCENE_LUT FPS_OPT DYN_YGAMMA_OPT RW_SENSOR_REG | |

## 6. AE/AWB 实时统计（GET_STA 回传结构 ISP_AE_AWB_INFO）

```
AE:  bv(f32) exposure_value(f32) exposure_line(u32) analog_gain(u16) final_luma_target(u16) ae_luma_avg(u16)
AWB: delta_cb/cr(u16) color_temp(u16) back/front_wp_cnt(u32) awb_rgb_mean[3] back/front_smooth_mean[3] r_gain b_gain(u16)
```
——闭环评价的"过程指标"来源（对应串口 image_isp_status() 同款数据）。

## 7. 参数基线三级（固化路径）

flash 参数（`ExportSingleParam`→isp.bin→txw82xApp 重编） > 驱动内嵌 `isp_iq_param` > `default_isp_param_init`。
CONFIG_SRAM_PARAM(106) 可在线写 SRAM 参数组（在线调优的落点），DUMP_SRAM_PARAM 读回。

## 8. autosp 对接要点

- `autosp/platform/txw.py`：TunningProtocol.build_cmd() 按本文档帧格式打包；pyserial 发送
- 先联调三个命令打通链路：GET_VERSION(1) → AE_LUMA_TARGET(55, 写+ACK) → GET_IMG(18, 收 JPEG)
- RAW 抓取（BLC/LSC 标定自动化）：GET_SENSOR_RAW(103)，固件需 DUAL_EN=1
- 统计轮询：GET_STA(0) 解析 ISP_AE_AWB_INFO → 评价层的"过程指标"
