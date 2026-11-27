"""Vygeneruje ikony appky – minimalistická varianta „Signal Noir“ jako appka: černé pozadí, signální červený kruh
s tlumenou září (jako #dot + #glow v index.html), bez textu.

Plný čtverec – zaoblení dělá iOS. Spouštět jen při změně vzhledu; potom zvednout CACHE v sw.js a deploy.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).parent
S = 1024
BG, RED = (7, 7, 7), (255, 59, 31)
R = 0.27 * S  # poloměr kruhu


def disc(radius: float, blur: float, alpha: int) -> Image.Image:
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).ellipse([S / 2 - radius, S / 2 - radius, S / 2 + radius, S / 2 + radius], fill=alpha)
    return m.filter(ImageFilter.GaussianBlur(blur)) if blur else m


img = Image.new("RGB", (S, S), BG)
red = Image.new("RGB", (S, S), RED)
# záře: široká slabá + užší silnější (odpovídá box-shadow 0 0 90px / 0 0 34px)
for radius, blur, alpha in ((R * 1.9, S * 0.12, 40), (R * 1.15, S * 0.05, 90)):
    img = Image.composite(red, img, disc(radius, blur, alpha))
# kruh s vyhlazeným okrajem (4× supersampling masky)
big = Image.new("L", (S * 4, S * 4), 0)
ImageDraw.Draw(big).ellipse([(S / 2 - R) * 4, (S / 2 - R) * 4, (S / 2 + R) * 4, (S / 2 + R) * 4], fill=255)
img = Image.composite(red, img, big.resize((S, S), Image.LANCZOS))
for size in (180, 192, 512):
    img.resize((size, size), Image.LANCZOS).save(HERE / f"icon-{size}.png")
print("ICONS_OK")
