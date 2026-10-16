"""앱 아이콘 생성 (빨강 양봉 + 파랑 음봉). python tools/make_icons.py"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
BG, UP, DOWN = (24, 32, 46), (231, 64, 72), (77, 135, 245)

def draw(size, safe=1.0, rounded=True):
    s = 4  # 슈퍼샘플링
    S = size * s
    im = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(im)
    u = S * safe / 24  # 24 단위 그리드
    o = S * (1 - safe) / 2
    def r(x0, y0, x1, y1, c, rad):
        d.rounded_rectangle([o + x0 * u, o + y0 * u, o + x1 * u, o + y1 * u], radius=rad * u, fill=c)
    # 양봉
    r(5.2, 3.6, 6.4, 19.6, UP, 0.6); r(3.6, 6.8, 8.0, 16.4, UP, 1.0)
    # 음봉
    r(15.6, 6.0, 16.8, 20.6, DOWN, 0.6); r(14.0, 9.6, 18.4, 17.6, DOWN, 1.0)
    return im.resize((size, size), Image.LANCZOS)

draw(180, 0.78).save(OUT / "icon-180.png")
draw(192, 0.78).save(OUT / "icon-192.png")
draw(512, 0.78).save(OUT / "icon-512.png")
draw(512, 0.62).save(OUT / "icon-512-maskable.png")
print("ok")
