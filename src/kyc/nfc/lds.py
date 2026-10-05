"""LDS data-group parsing: BER-TLV, DG1 (MRZ), DG2 (portrait), DG15 (AA public key)."""

from dataclasses import dataclass

from cryptography.hazmat.primitives.serialization import load_der_public_key


class LDSError(ValueError):
    pass


def read_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    """(tag, value, next offset) for one BER-TLV element; multi-byte tags and long lengths."""
    if offset >= len(data):
        raise LDSError("Truncated TLV")
    tag = data[offset]
    offset += 1
    if tag & 0x1F == 0x1F:
        while True:
            if offset >= len(data):
                raise LDSError("Truncated tag")
            tag = (tag << 8) | data[offset]
            offset += 1
            if not data[offset - 1] & 0x80:
                break
    if offset >= len(data):
        raise LDSError("Missing length")
    length = data[offset]
    offset += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise LDSError("Unsupported length")
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    if offset + length > len(data):
        raise LDSError("Value exceeds data")
    return tag, data[offset:offset + length], offset + length


def children(value: bytes) -> list[tuple[int, bytes]]:
    items, offset = [], 0
    while offset < len(value):
        tag, inner, offset = read_tlv(value, offset)
        items.append((tag, inner))
    return items


def find(value: bytes, wanted: int) -> bytes | None:
    """Depth-first search for the first element with this tag (constructed tags are descended)."""
    for tag, inner in children(value):
        if tag == wanted:
            return inner
        constructed = (tag >> ((tag.bit_length() - 1) // 8 * 8)) & 0x20
        if constructed:
            try:
                found = find(inner, wanted)
            except LDSError:
                continue
            if found is not None:
                return found
    return None


def dg1_mrz(dg1: bytes) -> list[str]:
    tag, value, _ = read_tlv(dg1)
    if tag != 0x61:
        raise LDSError("DG1 must start with tag 61")
    mrz = find(value, 0x5F1F)
    if mrz is None:
        raise LDSError("DG1 has no MRZ (5F1F)")
    text = mrz.decode("ascii", errors="strict")
    for width in (44, 36, 30):
        if len(text) % width == 0 and len(text) // width in (2, 3):
            return [text[index:index + width] for index in range(0, len(text), width)]
    raise LDSError("DG1 MRZ has an unexpected length")


@dataclass(frozen=True)
class Portrait:
    data: bytes
    media_type: str


def dg2_portrait(dg2: bytes) -> Portrait:
    """The facial image inside ISO/IEC 19794-5 biometric data (JPEG or JPEG 2000)."""
    tag, value, _ = read_tlv(dg2)
    if tag != 0x75:
        raise LDSError("DG2 must start with tag 75")
    block = find(value, 0x5F2E) or find(value, 0x7F2E) or value
    signatures = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x00\x00\x00\x0cjP  ", "image/jp2"), (b"\xff\x4f\xff\x51", "image/j2k"))
    for marker, media_type in signatures:
        index = block.find(marker)
        if index >= 0:
            return Portrait(block[index:], media_type)
    raise LDSError("DG2 contains no JPEG or JPEG 2000 image")


def dg15_public_key(dg15: bytes):
    tag, value, _ = read_tlv(dg15)
    if tag != 0x6F:
        raise LDSError("DG15 must start with tag 6F")
    try:
        return load_der_public_key(value)
    except ValueError as error:
        raise LDSError("DG15 public key is unreadable") from error
