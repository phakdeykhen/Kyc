"""Reads fraud-relevant markers from an uploaded file's own metadata.

Only three facts leave this module: a recognized editor name, a screenshot marker and
the capture date. GPS, device serials and every other tag are ignored and never stored.
"""

from datetime import date, datetime
from io import BytesIO
import re

from PIL import Image, UnidentifiedImageError

EDITORS = ("photoshop", "gimp", "lightroom", "pixelmator", "affinity photo", "paint.net", "picsart", "snapseed",
           "canva", "fotor", "photopea", "facetune", "remini", "meitu", "photoroom")
SCREENSHOT = re.compile(rb"screenshot", re.IGNORECASE)


def read(data: bytes) -> tuple[str | None, bool, date | None]:
    try:
        with Image.open(BytesIO(data)) as image:
            exif = image.getexif()
            texts = [str(exif.get(tag, "")) for tag in (0x0131, 0x010E)]          # Software, ImageDescription
            details = exif.get_ifd(0x8769)                                       # Exif IFD
            comment = details.get(0x9286, b"")                                   # UserComment
            texts.append(comment.decode("latin-1", "ignore") if isinstance(comment, bytes) else str(comment))
            texts.extend(str(image.info.get(key, "")) for key in ("Software", "Description", "Comment"))
            xmp = image.info.get("XML:com.adobe.xmp") or image.info.get("xmp") or b""
            xmp = xmp.encode() if isinstance(xmp, str) else xmp
            original = details.get(0x9003)                                       # DateTimeOriginal
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None, False, None
    joined = " ".join(texts).lower()
    editor = next((name for name in EDITORS if name in joined or name.encode() in xmp.lower()), None)
    screenshot = "screenshot" in joined or bool(SCREENSHOT.search(xmp))
    captured = None
    if original:
        try:
            captured = datetime.strptime(str(original).strip()[:19], "%Y:%m:%d %H:%M:%S").date()
        except ValueError:
            captured = None
    return editor, screenshot, captured
