"""
生成 student-app 桌面图标（暖色系）的 PNG 回退资源。

背景：Android 8.0（API 26）以下不支持 adaptive-icon，Manifest 引用的
@mipmap/ic_launcher 必须有真实位图，否则在 Android 7.x 上会资源解析失败。
因此本脚本把 res/drawable/ic_launcher_*.xml 里的同一套矢量图形
（暖色渐变底 + 白色书本 + 琥珀星）光栅化到各密度目录。

设计要点：
  · 图形坐标沿用矢量资源的 108dp 视口，缩放后落盘，与 XML 严格一致；
  · 4 倍超采样 + LANCZOS 降采样代替矢量抗锯齿；
  · 方形图标用圆角方形（半径 22%），圆形图标用正圆，两种遮罩都不裁到主体
    （所有图形都在中心 66dp 安全圆内）。

用法（在项目根执行，落盘到 app/src/main/res/mipmap-*）：
    python tools/gen_launcher_icon.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

# —— 视口与画布 ——
VIEWPORT = 108.0
DENSITIES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}
SUPERSAMPLE = 4

# —— 配色（与 TesEduColors.Brand / BrandViolet / Amber 同源）——
BG_STOPS = [  # (offset, R, G, B)
    (0.0, 0xFF, 0x7A, 0x45),   # 烈焰橙
    (0.55, 0xFF, 0x5A, 0x50),  # 过渡
    (1.0, 0xFF, 0x3D, 0x81),   # 热情玫红
]
BOOK = (255, 255, 255, 255)
STAR = (255, 0xD3, 0x4D, 255)

# —— 图形轮廓（三次贝塞尔段，坐标取自 ic_launcher_foreground.xml）——
BOOK_LEFT = [
    ("C", (52, 50), (45.5, 46.5), (38, 45), (32, 45.5)),
    ("L", (32, 73.5)),
    ("C", (32, 73.5), (38, 73), (45.5, 74.5), (52, 78)),
]
BOOK_RIGHT = [
    ("C", (56, 50), (62.5, 46.5), (70, 45), (76, 45.5)),
    ("L", (76, 73.5)),
    ("C", (76, 73.5), (70, 73), (62.5, 74.5), (56, 78)),
]
STAR_MAIN = [
    ("C", (54, 22), (55.4, 28.6), (57.4, 30.6), (64, 32)),
    ("C", (64, 32), (57.4, 33.4), (55.4, 35.4), (54, 42)),
    ("C", (54, 42), (52.6, 35.4), (50.6, 33.4), (44, 32)),
    ("C", (44, 32), (50.6, 30.6), (52.6, 28.6), (54, 22)),
]
STAR_SUB = [
    ("C", (70, 25.5), (70.5, 28), (70.9, 28.4), (73.5, 29)),
    ("C", (73.5, 29), (70.9, 29.6), (70.5, 30), (70, 32.5)),
    ("C", (70, 32.5), (69.5, 30), (69.1, 29.6), (66.5, 29)),
    ("C", (66.5, 29), (69.1, 28.4), (69.5, 28), (70, 25.5)),
]


def cubic(p0, p1, p2, p3, steps=28):
    """采样三次贝塞尔，返回折线点列（不含起点，由调用方接续）。"""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        m = 1 - t
        x = m**3 * p0[0] + 3 * m * m * t * p1[0] + 3 * m * t * t * p2[0] + t**3 * p3[0]
        y = m**3 * p0[1] + 3 * m * m * t * p1[1] + 3 * m * t * t * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def outline(segments):
    pts = [segments[0][1]]  # 起点取第一段首控制点
    for seg in segments:
        if seg[0] == "C":
            _, p0, p1, p2, p3 = seg
            pts += cubic(p0, p1, p2, p3)
        else:
            pts.append(seg[1])
    return pts


def lerp_color(t):
    for i in range(len(BG_STOPS) - 1):
        o0, c0 = BG_STOPS[i][0], BG_STOPS[i][1:]
        o1, c1 = BG_STOPS[i + 1][0], BG_STOPS[i + 1][1:]
        if t <= o1:
            k = 0.0 if o1 == o0 else (t - o0) / (o1 - o0)
            return tuple(int(round(a + (b - a) * k)) for a, b in zip(c0, c1))
    return BG_STOPS[-1][1:]


def gradient(size):
    """对角线性渐变底图。"""
    img = Image.new("RGB", (size, size))
    px = []
    denom = max(1, 2 * size - 2)
    for y in range(size):
        row = (0, 0)  # 占位，下面按行填充
        px.append(row)
    data = []
    for y in range(size):
        for x in range(size):
            data.append(lerp_color((x + y) / denom))
    img.putdata(data)
    return img


def mask_for(size, round_shape: bool):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    if round_shape:
        d.ellipse((0, 0, size - 1, size - 1), fill=255)
    else:
        d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255)
    return m


def render(size, round_shape: bool):
    ss = size * SUPERSAMPLE
    scale = ss / VIEWPORT

    base = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    grad = gradient(ss)
    base.paste(grad, (0, 0), mask_for(ss, round_shape))

    d = ImageDraw.Draw(base)
    for shape, color in (
        (BOOK_LEFT, BOOK),
        (BOOK_RIGHT, BOOK),
        (STAR_MAIN, STAR),
        (STAR_SUB, STAR),
    ):
        pts = [(x * scale, y * scale) for x, y in outline(shape)]
        d.polygon(pts, fill=color)

    return base.resize((size, size), Image.LANCZOS)


def main():
    # 脚本位于 <项目根>/tools/，资源在 <项目根>/app/src/main/res/
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.join(project, "app", "src", "main", "res")
    for density, size in DENSITIES.items():
        d = os.path.join(root, f"mipmap-{density}")
        os.makedirs(d, exist_ok=True)
        for name, is_round in (("ic_launcher.png", False), ("ic_launcher_round.png", True)):
            img = render(size, is_round)
            path = os.path.join(d, name)
            img.save(path, "PNG", optimize=True)
            print(f"  {density:7s} {size:3d}px  {name:22s} {os.path.getsize(path):7d} B")


if __name__ == "__main__":
    print("生成 student-app 暖色图标 PNG：")
    main()
