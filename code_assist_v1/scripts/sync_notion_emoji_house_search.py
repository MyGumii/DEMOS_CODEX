"""Bundle the 20 emoji results observed for Notion's Korean `집` search.

The order and labels were read from the signed-in picker on 2026-09-27.
Artwork comes from the pinned, licensed Twemoji v14.0.2 assets.
"""

from __future__ import annotations

import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from sync_notion_emoji_faces import CATALOG, fetch_asset


OBSERVED = """🏠|집|자연과 장소
🛖|오두막|자연과 장소
🏘️|주택 건물|자연과 장소
🪴|분재|자연과 장소
🪹|빈 둥지|자연과 장소
🏚️|낡은 주택 건물|자연과 장소
🦞|바닷가재|자연과 장소
😣|참아내는 얼굴|사람
🐈|고양이|자연과 장소
🫳|손바닥을 아래로 향한 손|사람
🙃|거꾸로 된 얼굴|사람
👌|오케이를 표시한 손|사람
🤌|꼬집는 손가락|사람
🤏|꼬집는 손|사람
🐚|달팽이집|자연과 장소
🍵|차|생활과 활동
🍺|맥주잔|생활과 활동
🏡|정원이 있는 집|자연과 장소
🏮|일본식 등|생활과 활동
🇪🇬|깃발: 이집트|기호와 상태"""


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    observed = [line.split("|", 2) for line in OBSERVED.splitlines()]
    values = [value for value, _, _ in observed]
    if len(values) != 20 or len(set(values)) != 20:
        raise ValueError("The observed house search must have 20 unique results")
    existing = {item["value"]: item for item in catalog["emojis"]}
    with ThreadPoolExecutor(max_workers=8) as workers:
        assets = list(workers.map(fetch_asset, values))
    for (value, label, group), asset in zip(observed, assets):
        if value in existing:
            continue  # Preserve prior category ordering and richer existing terms.
        english = " ".join(
            unicodedata.name(char, "").lower()
            for char in value if char not in {"\ufe0f", "\u200d"}
        )
        item = {"value": value, "group": group, "label": label,
                "terms": f"{label} {english}".strip(), "asset": asset}
        catalog["emojis"].append(item)
        existing[value] = item
    catalog.setdefault("observedSearch", {})["집"] = values
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bundled {len(values)} observed house-search results; catalog has {len(catalog['emojis'])} entries")


if __name__ == "__main__":
    main()
