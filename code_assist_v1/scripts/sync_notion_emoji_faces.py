"""Sync the first visible Notion Korean emoji category with pinned Twemoji SVGs.

The order and labels below were read from the signed-in Notion icon picker on
2026-09-27. Twemoji v14.0.2 supplies the matching, licensed artwork.
"""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "notion_static/assets/icons/catalog.json"
ASSETS = ROOT / "notion_static/assets/emoji"
TWEMOJI = "https://raw.githubusercontent.com/twitter/twemoji/v14.0.2/assets/svg"
FACES = """😀|활짝 웃는 얼굴
😃|눈을 크게 뜨고 웃는 얼굴
😄|미소 짓는 눈으로 활짝 웃는 얼굴
😁|미소 짓는 눈으로 웃는 얼굴
😆|눈웃음짓는 얼굴
😅|땀 흘리며 웃는 얼굴
🤣|바닥을 구르며 웃는 얼굴
😂|기쁨의 눈물을 흘리는 얼굴
🙂|살짝 미소 짓는 얼굴
🙃|거꾸로 된 얼굴
🫠|녹아 내리는 얼굴
😉|윙크하는 얼굴
😊|미소 짓는 눈으로 살짝 웃는 얼굴
😇|후광이 비치는 웃는 얼굴
🥰|하트와 함께 웃는 얼굴
😍|하트 눈 얼굴
🤩|반한 얼굴
😘|키스를 보내는 얼굴
😗|키스하는 얼굴
☺️|웃는 얼굴
😚|눈을 감은 채로 키스하는 얼굴
😙|미소 짓는 눈으로 키스하는 얼굴
🥲|눈물 흘리며 웃는 얼굴
😋|맛있는 음식을 음미하는 얼굴
😛|혀를 내민 얼굴
😜|윙크하면서 혀를 내민 얼굴
🤪|괴짜 얼굴
😝|눈을 감고 메롱하는 얼굴
🤑|돈 모양의 입이 있는 얼굴
🤗|포옹하고 있는 얼굴
🤭|손으로 입을 가린 얼굴
🫢|눈을 뜨고 손으로 입을 가린 얼굴
🫣|한쪽 눈을 가리고 훔쳐보는 얼굴
🤫|쉿 하는 얼굴
🤔|생각하는 얼굴
🫡|경례하는 얼굴
🤐|지퍼로 입을 잠근 얼굴
🤨|눈썹을 치켜올린 얼굴
😐|덤덤한 얼굴
😑|무표정한 얼굴
😶|입이 없는 얼굴
🫥|테두리가 점선으로 된 얼굴
😶‍🌫️|공상에 잠긴 얼굴
😏|히죽거리는 얼굴
😒|지루해하는 얼굴
🙄|눈을 굴리고 있는 얼굴
😬|찌푸린 얼굴
😮‍💨|날숨 쉬는 얼굴
🤥|거짓말하는 얼굴
😌|안심한 얼굴
😔|수심 어린 얼굴
😪|졸린 얼굴
🤤|침 흘리는 얼굴
😴|졸고 있는 얼굴
😷|마스크 낀 얼굴
🤒|체온계를 물고 있는 얼굴
🤕|머리에 붕대를 감은 얼굴
🤢|구역질을 하는 얼굴
🤮|토하는 얼굴
🤧|재채기하는 얼굴
🥵|더운 얼굴
🥶|추워하는 얼굴
🥴|헤롱헤롱 얼굴
😵|어지러운 얼굴
😵‍💫|현기증 난 얼굴
🤯|폭발하는 얼굴
🤠|카우보이 모자 쓴 얼굴
🥳|파티하는 얼굴
🥸|변장한 얼굴
😎|선글라스 낀 얼굴
🤓|모범생 얼굴
🧐|단안경을 쓴 얼굴"""


def stems(value: str) -> list[str]:
    codepoints = [f"{ord(char):x}" for char in value]
    without_presentation = [part for part in codepoints if part != "fe0f"]
    return list(dict.fromkeys(("-".join(without_presentation), "-".join(codepoints))))


def fetch_asset(value: str) -> str:
    for stem in stems(value):
        destination = ASSETS / f"{stem}.svg"
        if destination.exists():
            return stem
        try:
            with urllib.request.urlopen(f"{TWEMOJI}/{stem}.svg", timeout=20) as response:
                source = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        root = ET.fromstring(source)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError(f"Unexpected SVG root for {value}")
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] in {"script", "foreignObject"}:
                raise ValueError(f"Unsafe SVG element for {value}")
            if any(key.rsplit("}", 1)[-1].lower().startswith("on") for key in element.attrib):
                raise ValueError(f"Unsafe SVG attribute for {value}")
        destination.write_bytes(source)
        return stem
    raise FileNotFoundError(f"Twemoji v14.0.2 has no artwork for {value}")


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    observed = [line.split("|", 1) for line in FACES.splitlines()]
    if len(observed) != 72 or len({value for value, _ in observed}) != 72:
        raise ValueError("The observed Notion face list must have 72 unique entries")
    existing = {item["value"]: item for item in catalog["emojis"]}
    with ThreadPoolExecutor(max_workers=8) as workers:
        assets = list(workers.map(fetch_asset, (value for value, _ in observed)))
    reordered = []
    for (value, label), asset in zip(observed, assets):
        previous = existing.pop(value, None)
        english = " ".join(
            unicodedata.name(char, "").lower()
            for char in value if char not in {"\ufe0f", "\u200d"}
        )
        previous_terms = previous["terms"] if previous else english
        terms = previous_terms if previous_terms.startswith(label + " ") else f"{label} {previous_terms}"
        reordered.append({
            "value": value,
            "group": "사람",
            "label": label,
            "terms": terms.strip(),
            "asset": previous["asset"] if previous else asset,
        })
    for item in catalog["emojis"]:
        if item["value"] in existing:
            if item["group"] == "표정과 사람":
                item["group"] = "사람"
            reordered.append(item)
    catalog["emojis"] = reordered
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synced {len(observed)} observed faces; catalog now has {len(reordered)} emojis")


if __name__ == "__main__":
    main()
