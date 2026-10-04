
# Pillow 10+ removed font.getsize(); restore it for PaperTTY
from PIL import ImageFont as _ImageFont
for _cls in (_ImageFont.FreeTypeFont, _ImageFont.ImageFont):
    if not hasattr(_cls, "getsize"):
        _cls.getsize = lambda self, text, *a, **k: self.getbbox(text, *a, **k)[2:]
