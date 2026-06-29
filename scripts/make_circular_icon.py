"""Process icon PNG: circular crop + luminance-to-alpha for transparent background."""

import sys
from pathlib import Path
from PIL import Image, ImageDraw


def make_circular(src: Path, dst: Path, size: int, padding_pct: float = 0.0) -> None:
    img = Image.open(src).convert("RGBA")
    w, h = img.size
    pad = int(min(w, h) * padding_pct)
    img = img.crop((pad, pad, w - pad, h - pad))
    img = img.resize((size, size), Image.LANCZOS)

    scale = 4
    big = size * scale
    mask_big = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask_big)
    draw.ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask_big.resize((size, size), Image.LANCZOS)

    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    result.save(dst, "PNG")
    print(f"Saved {dst} ({size}x{size})")


def make_luminance_alpha(src: Path, dst: Path, size: int, padding_pct: float = 0.0) -> None:
    """Black pixels become transparent; bright glow stays fully opaque."""
    img = Image.open(src).convert("RGBA")
    w, h = img.size
    pad = int(min(w, h) * padding_pct)
    img = img.crop((pad, pad, w - pad, h - pad))
    img = img.resize((size, size), Image.LANCZOS)

    r, g, b, a = img.split()
    # Luminance as alpha: bright teal/white = opaque, black = transparent
    luminance = img.convert("L")
    result = Image.merge("RGBA", (r, g, b, luminance))
    result.save(dst, "PNG")
    print(f"Saved {dst} ({size}x{size})")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/icons/gen-4/network-c.png")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("web/public")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Header logo: luminance-to-alpha so black disappears on any background
    make_luminance_alpha(src, out_dir / "logo.png", size=512, padding_pct=0.06)
    # Favicon: circular crop (holds up better at 64px)
    make_circular(src, out_dir / "favicon.png", size=64, padding_pct=0.08)
