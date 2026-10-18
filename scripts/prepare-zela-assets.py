"""Package the supplied official logo; crop margins and resize, never redraw it."""

from pathlib import Path
from shutil import copyfile

from PIL import Image

root = Path(__file__).resolve().parents[1]
source = root.parent / "Log\u00f3tipo Zela.me em fundo branco.png"
target = root / "frontend/public/brand"
target.mkdir(parents=True, exist_ok=True)
copyfile(source, target / "zela-original.png")
logo = Image.open(source).convert("RGBA")
assert logo.size == (1536, 1024), "Verifique o enquadramento do novo ficheiro oficial."
# The supplied sheet contains the horizontal Zela.me wordmark and a separate
# application icon below it. Preserve the official colors and white backing.
logo.crop((166, 289, 1385, 571)).save(target / "zela-logo.png", optimize=True)
mark = logo.crop((637, 620, 900, 869))
icon = Image.new("RGBA", (280, 280), "white")
icon.alpha_composite(mark, ((280 - mark.width) // 2, (280 - mark.height) // 2))
icon.save(target / "zela-symbol.png", optimize=True)
icon.resize((256, 256), Image.Resampling.LANCZOS).save(target / "favicon.png")
icon.save(target / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
icon.resize((180, 180), Image.Resampling.LANCZOS).save(target / "apple-touch-icon.png")
print("Official Zela.me wordmark and application icon packaged without recoloring or redrawing.")
