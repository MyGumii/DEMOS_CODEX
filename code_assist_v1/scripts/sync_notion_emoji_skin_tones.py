"""Bundle Twemoji skin-tone variants for the picker's available hand emojis.

Run after sync_notion_emoji_faces.py. Variants are catalogued for consistent
page, workspace, public and export rendering, but hidden from the base grid.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from sync_notion_emoji_faces import CATALOG, fetch_asset


BASES = {"✋": ("손바닥", "palm hand"), "👋": ("인사", "waving hand"),
         "💪": ("힘", "flexed biceps"), "👍": ("좋아요", "thumbs up"),
         "🙏": ("감사", "folded hands")}
MODIFIERS = (("🏻", "밝은 피부"), ("🏼", "약간 밝은 피부"),
             ("🏽", "중간 피부"), ("🏾", "약간 어두운 피부"),
             ("🏿", "어두운 피부"))


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    known = {item["value"]: item for item in catalog["emojis"]}
    values = ["✋", *(base + modifier for base in BASES for modifier, _ in MODIFIERS)]
    with ThreadPoolExecutor(max_workers=8) as workers:
        assets = dict(zip(values, workers.map(fetch_asset, values)))
    if "✋" not in known:
        first_other = next(index for index, item in enumerate(catalog["emojis"])
                           if item["group"] != "사람")
        palm = {"value": "✋", "group": "사람", "label": "손바닥",
                "terms": "손바닥 hand palm 피부색", "asset": assets["✋"]}
        catalog["emojis"].insert(first_other, palm)
        known["✋"] = palm
    for base, (label, english) in BASES.items():
        known[base]["skinTone"] = True
        for index, (modifier, tone_label) in enumerate(MODIFIERS, 1):
            value = base + modifier
            variant = known.get(value)
            if variant is None:
                variant = {"value": value, "group": "사람", "label": f"{label} · {tone_label}",
                           "terms": f"{label} {english} {tone_label}", "asset": assets[value],
                           "hidden": True, "toneBase": base, "tone": index}
                catalog["emojis"].append(variant)
                known[value] = variant
            else:
                variant.update(asset=assets[value], hidden=True, toneBase=base, tone=index)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bundled five skin tones for {len(BASES)} gestures; catalog has {len(catalog['emojis'])} entries")


if __name__ == "__main__":
    main()
