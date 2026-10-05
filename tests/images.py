"""Synthetic, non-personal capture fixtures. No real identity documents are used."""

from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CARD_RATIO = 85.6 / 53.98
PASSPORT_RATIO = 125.0 / 88.0


def card(width=1000, ratio=CARD_RATIO, tint=(214, 226, 236)):
    return _card(width, ratio, tint).copy()


@lru_cache(maxsize=16)
def _card(width, ratio, tint):
    height = round(width / ratio)
    image = Image.new("RGB", (width, height), tint)
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(7)
    for k in range(0, width, 9):  # fine guilloche-like security texture
        draw.arc((k - width // 2, -height // 3, k + width // 2, height + height // 3), 200, 340, fill=(190, 205, 222), width=1)
    portrait = (int(width * 0.06), int(height * 0.22), int(width * 0.30), int(height * 0.86))
    draw.rectangle(portrait, fill=(236, 240, 244), outline=(120, 130, 140), width=2)
    px0, py0, px1, py1 = portrait
    draw.ellipse((px0 + (px1 - px0) * 0.25, py0 + (py1 - py0) * 0.15, px1 - (px1 - px0) * 0.25, py0 + (py1 - py0) * 0.55), fill=(150, 120, 100))
    draw.rectangle((px0 + (px1 - px0) * 0.12, py0 + (py1 - py0) * 0.6, px1 - (px1 - px0) * 0.12, py1 - 2), fill=(70, 80, 100))
    draw.rectangle((0, 0, width, int(height * 0.14)), fill=(40, 70, 120))
    for row in range(7):
        y = int(height * (0.24 + row * 0.095))
        x = int(width * 0.36)
        for _ in range(int(rng.integers(8, 16))):
            w = int(rng.integers(12, 46))
            draw.rectangle((x, y, x + w, y + int(height * 0.035)), fill=(30, 35, 45))
            x += w + int(rng.integers(6, 12))
            if x > width * 0.94:
                break
    return image


@lru_cache(maxsize=8)
def _background(size, background, seed):
    rng = np.random.default_rng(seed)
    base = np.clip(np.array(background, dtype=np.float32) + rng.normal(0, 6, (size[1], size[0], 3)), 0, 255)
    return Image.fromarray(base.astype(np.uint8))


def scene(document=None, size=(1600, 1200), doc_width=1000, center=(0.5, 0.5), background=(58, 48, 44), seed=3):
    image = _background(size, background, seed).copy()
    document = document or card(doc_width)
    x = round(size[0] * center[0] - document.width / 2)
    y = round(size[1] * center[1] - document.height / 2)
    image.paste(document, (x, y), document if document.mode == "RGBA" else None)
    return image


def perspective(image, strength=0.25):
    width, height = image.size
    inset = width * strength
    # Map output quad corners back to the source rectangle (keystone towards the top).
    source = [(0, 0), (width, 0), (width, height), (0, height)]
    target = [(inset, 0), (width - inset, 0), (width, height), (0, height)]
    matrix = []
    for (x, y), (u, v) in zip(target, source):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    coefficients = np.linalg.solve(np.array(matrix), np.array(source).reshape(8))
    return image.convert("RGBA").transform(image.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)


def encode(image, fmt="JPEG", **options):
    buffer = BytesIO()
    image.save(buffer, fmt, **({"quality": 92} | options if fmt == "JPEG" else options))
    return buffer.getvalue()


def good():
    return scene()


def blurred():
    return scene().filter(ImageFilter.GaussianBlur(5))


def dark():
    return Image.fromarray((np.asarray(scene(), dtype=np.float32) * 0.12).astype(np.uint8))


def overexposed():
    return Image.fromarray(np.clip(np.asarray(scene(), dtype=np.float32) * 1.9 + 40, 0, 255).astype(np.uint8))


def glare():
    image = scene()
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, 420, 1000, 720), fill=(255, 255, 255))
    return image


def shadow():
    document = card()
    pixels = np.asarray(document, dtype=np.float32).copy()
    pixels[:, pixels.shape[1] // 2:] *= 0.55
    return scene(Image.fromarray(pixels.astype(np.uint8)))


def too_far():
    return scene(doc_width=380)


def too_close():
    return scene(size=(1600, 1040), doc_width=1590)


def cropped():
    return scene(center=(0.82, 0.5))


def two_documents():
    image = scene(doc_width=600, center=(0.27, 0.5))
    other = card(600)
    image.paste(other, (round(1600 * 0.73 - 300), round(600 - other.height / 2)))
    return image


def skewed():
    return scene(perspective(card(), 0.28), background=(58, 48, 44))


def low_resolution():
    return scene(size=(640, 480), doc_width=420)


def blank():
    return Image.new("RGB", (1600, 1200), (58, 48, 44))


def passport_page():
    return scene(card(1000, PASSPORT_RATIO, tint=(228, 220, 206)))


# --- Phase 3: fictional Cambodian National ID specimen (synthetic data, marked SPECIMEN) ---
KHMER_FONT = "/System/Library/Fonts/Supplemental/Khmer Sangam MN.ttf"
LATIN_FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
SPECIMEN = {
    "number": "០១០២០៣០៤០", "name_km": "សុខ សុភា", "name_latin": "SOK SOPHEA", "dob": "១៥.០៣.១៩៩០",
    "sex": "ស្រី", "height": "១៦០ ស.ម", "pob": "ភ្នំពេញ", "address": "ផ្ទះលេខ ១២ ផ្លូវ ២៧១ ភ្នំពេញ",
    "issue": "០១.០១.២០២០", "expiry": "៣១.១២.២០២៩",
}


def fonts_available():
    from pathlib import Path
    from PIL import features
    return Path(KHMER_FONT).exists() and Path(LATIN_FONT).exists() and features.check("raqm")


def kh_id_front(width=1600, **overrides):
    from PIL import ImageFont
    data = SPECIMEN | overrides
    height = round(width / CARD_RATIO)
    image = Image.new("RGB", (width, height), (236, 238, 230))
    draw = ImageDraw.Draw(image)
    km = lambda size: ImageFont.truetype(KHMER_FONT, size, layout_engine=ImageFont.Layout.RAQM)  # noqa: E731
    latin = lambda size: ImageFont.truetype(LATIN_FONT, size)  # noqa: E731
    draw.rectangle((40, 250, 400, 700), fill=(214, 222, 232), outline=(140, 150, 160), width=3)  # portrait area
    draw.ellipse((130, 300, 310, 500), fill=(170, 140, 120))
    draw.text((width // 2, 30), "ព្រះរាជាណាចក្រកម្ពុជា", font=km(46), fill=(20, 30, 60), anchor="mt")
    draw.text((width // 2, 105), "អត្តសញ្ញាណប័ណ្ណ", font=km(40), fill=(20, 30, 60), anchor="mt")
    draw.text((width - 60, 170), data["number"], font=km(46), fill=(160, 20, 20), anchor="rt")
    x, size = 440, 38
    rows = [f"គោត្តនាម និងនាម: {data['name_km']}", None,
            f"ថ្ងៃខែឆ្នាំកំណើត: {data['dob']} ភេទ: {data['sex']} កម្ពស់: {data['height']}",
            f"ទីកន្លែងកំណើត: {data['pob']}", f"អាសយដ្ឋាន: {data['address']}",
            f"សុពលភាព: {data['issue']} ដល់ថ្ងៃ {data['expiry']}", "ភិនភាគ: ប្រជ្រុយ"]
    y = 250
    for row in rows:
        if row is None:
            draw.text((x, y), data["name_latin"], font=latin(40), fill=(10, 10, 10))
        else:
            draw.text((x, y), row, font=km(size), fill=(10, 10, 10))
        y += 85
    draw.text((60, height - 60), "SPECIMEN", font=latin(28), fill=(180, 60, 60))
    return image


def kh_id_back(width=1600):
    from PIL import ImageFont
    height = round(width / CARD_RATIO)
    image = Image.new("RGB", (width, height), (232, 236, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 60, 420, 460), outline=(120, 120, 120), width=3)  # fingerprint box
    mono = ImageFont.truetype("/System/Library/Fonts/Supplemental/Courier New Bold.ttf", 56)
    from tests.mrz_build import td1
    for index, line in enumerate(td1()):
        draw.text((60, height - 300 + index * 85), line, font=mono, fill=(10, 10, 10))
    return image


def photographed(document, doc_width=1200):
    """Place a rendered document on a desk-like background, as a phone photo would."""
    scaled = document.resize((doc_width, round(doc_width * document.height / document.width)), Image.Resampling.LANCZOS)
    return scene(scaled, size=(1800, 1350))


# --- Phase 4: fictional Cambodian NSSF member card specimen (synthetic data, marked SPECIMEN) ---
NSSF_SPECIMEN = {
    "member": "០០១២៣៤៥៦៧៨", "name_km": "ចាន់ ដារ៉ា", "name_latin": "CHAN DARA", "sex": "ប្រុស", "dob": "០២.០៧.១៩៨៨",
    "nid": "០៩០៨០៧០៦០", "employer": "ក្រុមហ៊ុន អង្គរ ផលិតកម្ម", "issue": "១០.០៥.២០២២",
}


def kh_nssf_front(width=1600, **overrides):
    from PIL import ImageFont
    data = NSSF_SPECIMEN | overrides
    height = round(width / CARD_RATIO)
    image = Image.new("RGB", (width, height), (232, 240, 236))
    draw = ImageDraw.Draw(image)
    km = lambda size: ImageFont.truetype(KHMER_FONT, size, layout_engine=ImageFont.Layout.RAQM)  # noqa: E731
    latin = lambda size: ImageFont.truetype(LATIN_FONT, size)  # noqa: E731
    draw.rectangle((0, 0, width, 150), fill=(214, 232, 222))
    draw.text((width // 2, 18), "ព្រះរាជាណាចក្រកម្ពុជា", font=km(40), fill=(20, 60, 40), anchor="mt")
    draw.text((width // 2, 80), "បេឡាជាតិរបបសន្តិសុខសង្គម", font=km(40), fill=(20, 60, 40), anchor="mt")
    draw.rectangle((40, 230, 380, 660), fill=(214, 226, 232), outline=(140, 150, 160), width=3)
    draw.ellipse((120, 280, 300, 470), fill=(160, 130, 110))
    rows = [f"ប័ណ្ណសមាជិក លេខសមាជិក: {data['member']}", f"គោត្តនាម និងនាម: {data['name_km']}", None,
            f"ភេទ: {data['sex']} ថ្ងៃខែឆ្នាំកំណើត: {data['dob']}", f"លេខអត្តសញ្ញាណប័ណ្ណ: {data['nid']}",
            f"ឈ្មោះសហគ្រាស: {data['employer']}", f"ថ្ងៃចេញប័ណ្ណ: {data['issue']}"]
    y = 190
    for row in rows:
        if row is None:
            draw.text((420, y), data["name_latin"], font=latin(40), fill=(10, 10, 10))
        else:
            draw.text((420, y), row, font=km(36), fill=(10, 10, 10))
        y += 80
    draw.text((60, height - 60), "SPECIMEN", font=latin(28), fill=(180, 60, 60))
    return image


def kh_nssf_back(width=1600):
    from PIL import ImageFont
    height = round(width / CARD_RATIO)
    image = Image.new("RGB", (width, height), (232, 240, 236))
    draw = ImageDraw.Draw(image)
    km = ImageFont.truetype(KHMER_FONT, 34, layout_engine=ImageFont.Layout.RAQM)
    for index, row in enumerate(("ចំណាំ៖ ប័ណ្ណនេះជាកម្មសិទ្ធិរបស់ ប.ស.ស", "សូមបង្ហាញប័ណ្ណនេះ នៅពេលទទួលសេវា")):
        draw.text((80, 120 + index * 80), row, font=km, fill=(20, 20, 20))
    draw.rectangle((width - 420, height - 420, width - 80, height - 80), fill=(20, 20, 20))  # QR placeholder
    for row in range(8):
        for column in range(8):
            if (row * 3 + column * 5) % 4 == 0:
                draw.rectangle((width - 400 + column * 40, height - 400 + row * 40,
                                width - 370 + column * 40, height - 370 + row * 40), fill=(240, 240, 240))
    return image


# --- Phase 5: fictional passport data pages (synthetic data, marked SPECIMEN) ---
MONO_FONT = "/System/Library/Fonts/Supplemental/Courier New Bold.ttf"


def passport_fonts_available():
    from pathlib import Path
    return fonts_available() and Path(MONO_FONT).exists()


def passport_data_page(rows, mrz_lines, header=(), width=1600):
    """rows: (label, value) pairs printed label-above-value; mrz_lines: two TD3 lines."""
    from PIL import ImageFont
    height = round(width / PASSPORT_RATIO)
    image = Image.new("RGB", (width, height), (238, 234, 222))
    draw = ImageDraw.Draw(image)
    km = lambda size: ImageFont.truetype(KHMER_FONT, size, layout_engine=ImageFont.Layout.RAQM)  # noqa: E731
    latin = lambda size: ImageFont.truetype(LATIN_FONT, size)  # noqa: E731
    for index, text in enumerate(header):
        draw.text((width // 2, 20 + index * 58), text, font=km(36), fill=(30, 40, 70), anchor="mt")
    draw.rectangle((50, 190, 400, 640), fill=(214, 222, 232), outline=(140, 150, 160), width=3)
    draw.ellipse((140, 240, 310, 430), fill=(170, 140, 120))
    y = 170
    for label, value in rows:
        draw.text((440, y), label, font=km(26), fill=(90, 90, 90))
        draw.text((440, y + 34), value, font=latin(36), fill=(10, 10, 10))
        y += 82
    mono = ImageFont.truetype(MONO_FONT, 52)
    for index, line in enumerate(mrz_lines):
        draw.text((40, height - 190 + index * 80), line, font=mono, fill=(10, 10, 10))
    draw.text((60, 660), "SPECIMEN", font=latin(28), fill=(180, 60, 60))
    return image


def kh_passport(**overrides):
    from tests.mrz_build import td3
    values = {"number": "N01234567", "surname": "SOK", "given": "SOPHEA", "dob": "15 MAR 1990", "sex": "F",
              "issue": "11 AUG 2020", "expiry": "11 AUG 2030", "mrz": td3()} | overrides
    rows = [("Passport No / លេខលិខិតឆ្លងដែន", values["number"]), ("Surname / នាមត្រកូល", values["surname"]),
            ("Given names / នាមខ្លួន", values["given"]), ("Nationality / សញ្ជាតិ", "CAMBODIAN"),
            ("Date of birth / ថ្ងៃខែឆ្នាំកំណើត", values["dob"]), ("Sex / ភេទ", values["sex"]),
            ("Date of issue / ថ្ងៃចេញ", values["issue"]), ("Date of expiry / ថ្ងៃផុតកំណត់", values["expiry"])]
    return passport_data_page(rows, values["mrz"], header=("ព្រះរាជាណាចក្រកម្ពុជា KINGDOM OF CAMBODIA", "លិខិតឆ្លងដែន PASSPORT"))


def foreign_passport():
    """ICAO 9303's fictional Utopia holder, with a future expiry so the document is current."""
    from tests.mrz_build import td3
    mrz = td3(state="UTO", number="L898902C3", nationality="UTO", birth="740812", sex="F", expiry="340415",
              surname="ERIKSSON", given="ANNA MARIA")
    rows = [("Passport No", "L898902C3"), ("Surname", "ERIKSSON"), ("Given names", "ANNA MARIA"),
            ("Nationality", "UTOPIAN"), ("Date of birth", "12 AUG 1974"), ("Sex", "F"), ("Date of expiry", "15 APR 2034")]
    return passport_data_page(rows, mrz, header=("UTOPIA", "PASSPORT"))
