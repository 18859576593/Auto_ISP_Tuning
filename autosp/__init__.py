# -*- coding: utf-8 -*-
"""autosp —— 自动 ISP 调参框架（Auto ISP Tuning Framework）

四层架构（对应四阶段路线）:
  platform/  L1 平台适配层   Phase0/2: 参数schema + 采图 + 实机通道（换平台只换这里）
  eval/      L2 评价层       客观指标(PSNR/SSIM/LPIPS可选) + VLM A/B 对比
  search/    L3 搜索层       随机/坐标下降(内置) + Optuna/OpenBox(可选后端)
  agent/     L4 编排层       指南知识库 + 规划器(LLM可选) + 会话主循环
"""
__version__ = "0.1.0"
