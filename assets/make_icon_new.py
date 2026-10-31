# -*- coding: utf-8 -*-
"""生成软件图标 —— 新版设计。

设计要点：
  1. 一眼认出是「硬盘」：驱动器外壳 + 盘片 + 主轴孔
  2. 一眼认出是「健康」：右下角绿色对勾徽章
  3. 16x16 也能看清：主体形状粗大、高对比，细节只放在大尺寸

用法：python make_icon_new.py            # 生成到 assets/icon.ico
      python make_icon_new.py --preview  # 只输出预览 PNG，不覆盖 ico
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icon.ico")

# 配色（与界面主题一致）
BG_TOP = (32, 37, 48, 255)      # 外壳渐变上部
BG_BOT = (20, 23, 30, 255)      # 外壳渐变下部
CASE_EDGE = (58, 66, 82, 255)   # 外壳描边
PLATTER = (74, 168, 255, 255)   # 盘片：界面主色蓝
PLATTER_HI = (124, 196, 255, 255)
HUB = (24, 27, 34, 255)         # 主轴孔（挖空色 = 底色）
GREEN = (61, 220, 132, 255)     # 健康绿
GREEN_DK = (42, 176, 100, 255)
HEAD = (158, 170, 190, 255)     # 读写臂


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))


def render(size: int) -> Image.Image:
    """按目标尺寸渲染。内部先放大 8 倍绘制再缩小，得到平滑边缘。"""
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # ---------- 外壳（圆角方块，竖向渐变）----------
    radius = int(S * 0.215)
    shell = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(shell)
    sd.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=_lerp(BG_TOP, BG_BOT, y / S))
    img.paste(grad, (0, 0), shell)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius,
                        outline=CASE_EDGE, width=max(1, int(S * 0.014)))

    # ---------- 盘片（同心圆 + 主轴孔）----------
    cx, cy = S * 0.50, S * 0.455
    r_out = S * 0.285
    r_hub = S * 0.075

    d.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=PLATTER)
    r_hi = r_out * 0.995
    d.arc([cx - r_hi, cy - r_hi, cx + r_hi, cy + r_hi],
          start=190, end=275, fill=PLATTER_HI, width=max(1, int(S * 0.026)))
    d.ellipse([cx - r_hub, cy - r_hub, cx + r_hub, cy + r_hub], fill=HUB)

    # ---------- 读写臂（只在较大尺寸画，小尺寸会糊）----------
    if size >= 48:
        arm_w = S * 0.030
        ax0, ay0 = cx + r_out * 0.62, cy - r_out * 0.62
        ax1, ay1 = cx + r_out * 0.06, cy - r_out * 0.06
        d.line([(ax0, ay0), (ax1, ay1)], fill=HEAD, width=int(arm_w))
        rr = arm_w * 1.15
        d.ellipse([ax0 - rr, ay0 - rr, ax0 + rr, ay0 + rr], fill=HEAD)

    # ---------- 健康对勾徽章（右下）----------
    bx, by = S * 0.735, S * 0.735
    br = S * 0.215
    pad = max(1, int(S * 0.020))
    d.ellipse([bx - br - pad, by - br - pad, bx + br + pad, by + br + pad], fill=BG_BOT)
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=GREEN)

    lw = max(1, int(S * 0.052))
    p1 = (bx - br * 0.42, by + br * 0.02)
    p2 = (bx - br * 0.08, by + br * 0.38)
    p3 = (bx + br * 0.48, by - br * 0.34)
    for a, b in ((p1, p2), (p2, p3)):
        d.line([a, b], fill=GREEN_DK, width=lw + max(1, int(S * 0.010)))
        d.line([a, b], fill=(255, 255, 255, 255), width=lw)

    return img.resize((size, size), Image.LANCZOS)


def main():
    argv = sys.argv[1:]
    # --out DIR：预览写到指定目录（默认脚本所在目录）
    outdir = HERE
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            outdir = argv[i + 1]
    preview = "--preview" in argv
    if preview:
        os.makedirs(outdir, exist_ok=True)
        for sz in (256, 128, 48, 32, 16):
            render(sz).save(os.path.join(outdir, f"preview-{sz}.png"))
        print(f"预览已生成到 {outdir}：preview-16/32/48/128/256.png")
        return

    base = render(256)
    base.save(OUT, format="ICO",
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"图标已生成: {OUT} ({os.path.getsize(OUT)} 字节)")


if __name__ == "__main__":
    main()
