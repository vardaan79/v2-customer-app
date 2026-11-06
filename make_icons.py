"""
make_icons.py — generate the PWA icon set.

Run once (or after a brand change):   python make_icons.py

Android/Chrome need 192 and 512 PNGs plus a maskable 512 (the OS crops a
maskable icon to whatever shape it likes, so the mark sits inside a safe circle
of 80% width with brand colour bleeding to the edges). iOS uses the 180px
apple-touch-icon and ignores the manifest.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', 'static', 'icons')
RED = (215, 25, 32)
INK = (32, 32, 32)
WHITE = (255, 255, 255)


def _font(size):
    for name in ('arialbd.ttf', 'seguibl.ttf', 'segoeuib.ttf', 'arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centre(draw, box, text, font, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (r - l)) / 2 - l
    y = box[1] + (box[3] - box[1] - (b - t)) / 2 - t
    draw.text((x, y), text, font=font, fill=fill)


def rounded(size, radius_ratio=0.22, bg=RED, pad=0.0):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    p = int(size * pad)
    d.rounded_rectangle([p, p, size - 1 - p, size - 1 - p],
                        radius=int(size * radius_ratio), fill=bg)
    _centre(d, (p, p, size - p, size - p), 'V2', _font(int(size * 0.46)), WHITE)
    return img


def maskable(size=512):
    """Full-bleed background; mark confined to the safe zone the OS won't crop."""
    img = Image.new('RGBA', (size, size), RED)
    d = ImageDraw.Draw(img)
    _centre(d, (0, 0, size, size), 'V2', _font(int(size * 0.34)), WHITE)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    made = []
    for size in (192, 512):
        p = os.path.join(OUT, f'icon-{size}.png')
        rounded(size).save(p)
        made.append(p)
    p = os.path.join(OUT, 'maskable-512.png')
    maskable().save(p)
    made.append(p)

    p = os.path.join(OUT, 'apple-touch-icon.png')   # iOS: no transparency, no rounding
    img = Image.new('RGB', (180, 180), RED)
    _centre(ImageDraw.Draw(img), (0, 0, 180, 180), 'V2', _font(82), WHITE)
    img.save(p)
    made.append(p)

    p = os.path.join(OUT, 'favicon-32.png')
    rounded(32, radius_ratio=0.25).save(p)
    made.append(p)

    for f in made:
        print(f'  {os.path.relpath(f, os.path.dirname(OUT))}  '
              f'{os.path.getsize(f):,} bytes')
    print(f'{len(made)} icons written to {OUT}')


if __name__ == '__main__':
    main()
