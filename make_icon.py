"""生成应用图标 assets/app.ico（多分辨率）。

设计：深蓝圆角底 + 白色地球经纬线 + 一枚橙色播报点，
      呼应「中东要闻 · 实时推送」。纯 Pillow 绘制，无需外部素材。
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def rounded_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return m


def draw_icon(size: int) -> Image.Image:
    """按目标尺寸等比绘制（大图缩小时线条会糊，所以直接按尺寸画）。"""
    ss = 4                                   # 超采样倍数，边缘更平滑
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- 背景：深蓝渐变（竖向）----
    top = (18, 42, 84)
    bottom = (10, 24, 52)
    for y in range(S):
        t = y / max(1, S - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (S, y)], fill=c + (255,))

    cx = cy = S / 2
    R = S * 0.315                            # 地球半径

    # ---- 地球本体：浅蓝圆 ----
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(64, 150, 214, 255))

    # ---- 经纬线（白色细线）----
    lw = max(1, int(S * 0.018))
    line = (255, 255, 255, 215)
    # 纬线：几条横向椭圆弧近似
    for frac in (-0.55, -0.18, 0.18, 0.55):
        y = cy + R * frac
        half = math.sqrt(max(0.0, R * R - (y - cy) ** 2))
        if frac == 0:
            d.line([(cx - half, y), (cx + half, y)], fill=line, width=lw)
        else:
            # 用扁椭圆画出弯曲的纬线
            ry = max(1.0, half * 0.30)
            d.ellipse([cx - half, y - ry, cx + half, y + ry],
                      outline=line, width=lw)
    # 经线：中间的竖线 + 两侧竖椭圆
    d.line([(cx, cy - R), (cx, cy + R)], fill=line, width=lw)
    for k in (0.42, 0.78):
        rx = R * k
        d.ellipse([cx - rx, cy - R, cx + rx, cy + R], outline=line, width=lw)

    # ---- 播报点：橙色实心圆 + 外圈脉冲 ----
    px, py = cx + R * 0.62, cy - R * 0.60
    pr = S * 0.105
    d.ellipse([px - pr * 1.75, py - pr * 1.75, px + pr * 1.75, py + pr * 1.75],
              outline=(245, 158, 66, 120), width=max(1, int(S * 0.014)))
    d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(245, 158, 66, 255))
    d.ellipse([px - pr * 0.35, py - pr * 0.35, px + pr * 0.35, py + pr * 0.35],
              fill=(255, 236, 210, 255))

    # ---- 裁成圆角 ----
    img = img.resize((size, size), Image.LANCZOS)
    img.putalpha(Image.composite(rounded_mask(size), Image.new("L", (size, size), 0),
                                 rounded_mask(size)))
    return img


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # 注意：Pillow 保存 ICO 时，append_images 里的图必须已经是最终尺寸，
    # 且要用 sizes 参数声明全部尺寸，否则只会写出第一张。
    frames = [draw_icon(s) for s in SIZES]
    base = frames[-1]                        # 以 256 为底图
    base.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES],
              append_images=frames[:-1])
    print("已生成:", OUT)
    with Image.open(OUT) as im:
        print("尺寸:", sorted(im.info.get("sizes", [])))
    print("大小:", os.path.getsize(OUT), "字节")


if __name__ == "__main__":
    main()
