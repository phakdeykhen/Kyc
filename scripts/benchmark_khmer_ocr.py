"""Run local Khmer field OCR; optional human transcriptions measure CER/field accuracy.

Examples:
  PYTHONPATH=src python scripts/benchmark_khmer_ocr.py ID/KhmerID/*.jpg --tessdata-dir var/models/tessdata-best
  Add --expected private-ground-truth.json to compare {filename: {field: printed_value}}.
Only aggregate counts are printed. --output contains personal data: keep it in var/.
"""

import argparse
from datetime import date
import json
from pathlib import Path
import time

from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.documents.field_ocr import read_khmer_fields
from kyc.documents.preprocess import normalize, prepare_side
from kyc.engines.capture_quality import decode_capture
from kyc.mrz.reader import read_mrz_lines
from kyc.ocr.tesseract import TesseractOCREngine


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--tessdata-dir")
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path, default=Path("var/khmer-field-benchmark.json"))
    args = parser.parse_args()
    truth = json.loads(args.expected.read_text()) if args.expected else {}
    ocr = TesseractOCREngine(tessdata_dir=args.tessdata_dir)
    adapter = CambodiaNationalIDAdapter()
    samples = []
    for path in args.images:
        started = time.monotonic()
        data = path.read_bytes()
        capture = decode_capture(data, max_bytes=len(data), max_pixels=24_000_000)
        prepared, located = prepare_side(capture.pixels, 85.6 / 53.98, normalize_text=False)
        region = adapter.layout.viz_regions["FRONT"]
        def locate(image):
            block = ocr.read_region(normalize(image), region, ("khm", "eng"))
            seen = {line.text for line in block}
            return sorted(block + [line for line in ocr.read_region(normalize(image), region, ("khm", "eng"), mode=11)
                                   if line.text not in seen], key=lambda line: (line.bbox[1], line.bbox[0]))
        visual = locate(prepared)
        if adapter.classify(visual, "FRONT").document_side != "FRONT":
            turned = prepared.rotate(180)
            alternative = locate(turned)
            if adapter.classify(alternative, "FRONT").confidence > adapter.classify(visual, "FRONT").confidence:
                prepared, visual = turned, alternative
        reads = visual + read_mrz_lines(ocr, normalize(prepared), adapter.layout.mrz_regions["FRONT"])
        classification = adapter.classify(reads, "FRONT")
        if classification.document_side != "FRONT":
            samples.append({"sample": path.name, "classification": "NOT_FRONT", "seconds": round(time.monotonic() - started, 2)})
            continue
        dedicated = read_khmer_fields(ocr, prepared, adapter, reads, located)
        doc = adapter.extract_fields({"FRONT": reads + dedicated, "BACK": []})
        fields = {}
        for item in doc.fields:
            if item.field not in adapter.layout.khmer_field_regions:
                continue
            value = item.normalized_value or ""
            expected = truth.get(path.name, {}).get(item.field)
            fields[item.field] = {"value": item.normalized_value, "confidence": item.confidence, "flags": item.flags,
                                  "dedicated_roi": any(line.notes[0] == f"FIELD_ROI:{item.field}" for line in dedicated)}
            if expected is not None:
                fields[item.field].update({"exact_match": value == expected,
                    "cer": round(edit_distance(value, expected) / max(1, len(expected)), 4)})
        samples.append({"sample": path.name, "classification": classification.document_side,
                        "located": located, "seconds": round(time.monotonic() - started, 2), "fields": fields,
                        "review_reasons": [reason for check in adapter.validate_fields(doc, date.today())
                                           for reason in check.reason_codes if check.result.value == "REVIEW"]})
    report = {"adapter": adapter.version, "engine": ocr.engine_version, "tessdata_dir": args.tessdata_dir,
              "human_ground_truth_provided": bool(truth), "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch(mode=0o600, exist_ok=True)
    args.output.chmod(0o600)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for sample in samples:
        print(json.dumps({"sample": sample["sample"], "classification": sample["classification"], "seconds": sample["seconds"],
                          "fields": {name: {"present": bool(field["value"]), "confidence": field["confidence"],
                                            "dedicated_roi": field["dedicated_roi"], "flags": field["flags"]}
                                     for name, field in sample.get("fields", {}).items()}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
