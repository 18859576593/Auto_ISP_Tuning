# -*- coding: utf-8 -*-
"""客观指标：PSNR / SSIM（内置，仅依赖 numpy+cv2）；LPIPS 为可选后端（需 torch）。

借鉴: refs/ISP-AutoTuning/Image_quality_assessment/FR_IQA.py（LPIPS 全参考）
"""
import numpy as np
import cv2


def load_img(path, size=None):
    img = cv2.imread(path)
    if size:
        img = cv2.resize(img, size)
    return img


def psnr(ref_path, test_path) -> float:
    """越高越好。返回正值；目标函数里取负。"""
    a, b = load_img(ref_path), load_img(test_path)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    if mse == 0:
        return 100.0
    return 10 * np.log10(255.0 ** 2 / mse)


def ssim(ref_path, test_path) -> float:
    """SSIM（0~1，越高越好）。经典公式，窗口 11x11 高斯。"""
    a, b = load_img(ref_path), load_img(test_path)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = cv2.getGaussianKernel(11, 1.5)
    w = np.outer(k, k.transpose())
    mu1, mu2 = cv2.filter2D(a, -1, w), cv2.filter2D(b, -1, w)
    s1, s2, s12 = (cv2.filter2D(x, -1, w) for x in ((a - mu1) ** 2, (b - mu2) ** 2, (a - mu1) * (b - mu2)))
    ssm = ((2 * mu1 * mu2 + C1) * (2 * s12 + C2)) / ((mu1 ** 2 + mu2 ** 2 + C1) * (s1 + s2 + C2))
    return float(np.mean(ssm))


def mae(ref_path, test_path) -> float:
    a, b = load_img(ref_path), load_img(test_path)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(np.mean(np.abs(a.astype(np.float64) - b.astype(np.float64))))


def _lpips_available():
    try:
        import torch  # noqa
        import lpips   # noqa
        return True
    except Exception:
        return False


def lpips_score(ref_path, test_path) -> float:
    """感知距离（越低越好）。可选后端：未装 torch/lpips 时抛出。"""
    if not _lpips_available():
        raise RuntimeError("lpips 需要 torch（Python3.14 暂不可用），用 psnr+ssim 兜底")
    import torch, lpips
    loss_fn = lpips.LPIPS(net="alex")
    def t(p):
        x = cv2.imread(p)[:, :, ::-1] / 127.5 - 1
        return torch.from_numpy(x.transpose(2, 0, 1))[None].float()
    with torch.no_grad():
        return float(loss_fn(t(ref_path), t(test_path)))


METRICS = {"psnr": psnr, "ssim": ssim, "mae": mae, "lpips": lpips_score}
