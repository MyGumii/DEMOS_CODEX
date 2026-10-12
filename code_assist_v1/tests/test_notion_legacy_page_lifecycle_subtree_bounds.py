from __future__ import annotations

import pytest

from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    LIFECYCLE_FILE_MANIFEST_DOMAIN,
    LIFECYCLE_MANIFEST_DOMAIN,
    LIFECYCLE_RECEIPT_DOMAIN,
    LIFECYCLE_RESULT_CONTRACTS,
    LegacyPageLifecycleContractError,
    MAX_LIFECYCLE_EFFECTS,
    canonical_json,
    digest,
    validate_lifecycle_receipt,
)
from code_assist_v1.notion_workspace_store import (
    MAX_PAGE_LIFECYCLE_SUBTREE_EFFECTS,
)


def _maximum_trash_receipt() -> dict[str, object]:
    request_sha256 = "sha256:" + "1" * 64
    effects: list[dict[str, object]] = []
    for index in range(MAX_LIFECYCLE_EFFECTS):
        page_id = "p_" + format(index, "032x")
        incarnation_id = "inc_" + format(index, "032x")
        transitioned = index == 0
        effects.append(
            {
                "source_page_id": page_id,
                "result_page_id": page_id,
                "source_incarnation_id": incarnation_id,
                "source_incarnation_generation": 1,
                "source_revision": 1,
                "source_change_generation": 1,
                "result_incarnation_id": incarnation_id,
                "result_incarnation_generation": 1,
                "result_revision": 1,
                "result_change_generation": 1,
                "before_lifecycle": "active",
                "after_lifecycle": "trashed" if transitioned else "active",
                "before_parent_source_page_id": "",
                "after_parent_source_page_id": "",
                "before_sibling_ordinal": 1,
                "after_sibling_ordinal": None if transitioned else 1,
                "transitioned": transitioned,
            }
        )
    receipt: dict[str, object] = {
        "contract": LIFECYCLE_RESULT_CONTRACTS["trash"],
        "action": "trash",
        "source_owner_subject_id": "owner",
        "root_source_page_id": effects[0]["source_page_id"],
        "result_root_source_page_id": effects[0]["result_page_id"],
        "source_request_sha256": request_sha256,
        "source_operation_receipt_sha256": "sha256:" + "0" * 64,
        "post_manifest_sha256": "sha256:" + "0" * 64,
        "file_manifest_sha256": digest(
            {
                "domain": LIFECYCLE_FILE_MANIFEST_DOMAIN,
                "action": "trash",
                "files": [],
            }
        ),
        "source_device": 1,
        "source_inode": 2,
        "source_link_count": 1,
        "source_epoch": 1,
        "full_sweep_generation": 1,
        "trash_source_operation_id": "",
        "placement_mode": "",
        "effects": effects,
        "mappings": [],
    }
    receipt["post_manifest_sha256"] = digest(
        {
            "domain": LIFECYCLE_MANIFEST_DOMAIN,
            "action": "trash",
            "source_owner_subject_id": "owner",
            "root_source_page_id": receipt["root_source_page_id"],
            "result_root_source_page_id": receipt["result_root_source_page_id"],
            "source_device": 1,
            "source_inode": 2,
            "source_link_count": 1,
            "source_epoch": 1,
            "full_sweep_generation": 1,
            "trash_source_operation_id": "",
            "placement_mode": "",
            "file_manifest_sha256": receipt["file_manifest_sha256"],
            "effects": effects,
            "mappings": [],
        }
    )
    body = dict(receipt)
    body.pop("source_operation_receipt_sha256")
    receipt["source_operation_receipt_sha256"] = digest(
        {"domain": LIFECYCLE_RECEIPT_DOMAIN, "result": body}
    )
    return receipt


def test_source_and_catalog_accept_the_same_exact_effect_bound() -> None:
    assert MAX_PAGE_LIFECYCLE_SUBTREE_EFFECTS == MAX_LIFECYCLE_EFFECTS == 11_004
    receipt = _maximum_trash_receipt()

    normalized = validate_lifecycle_receipt(
        receipt,
        expected_action="trash",
        expected_request_sha256=receipt["source_request_sha256"],
    )

    assert len(normalized.effects) == MAX_LIFECYCLE_EFFECTS
    assert len(canonical_json(normalized.descriptor()).encode("utf-8")) < 16 * 1024 * 1024


def test_effect_overflow_is_rejected_before_any_effect_is_parsed() -> None:
    receipt = _maximum_trash_receipt()
    receipt["effects"] = [object()] * (MAX_LIFECYCLE_EFFECTS + 1)

    with pytest.raises(
        LegacyPageLifecycleContractError,
        match="legacy page lifecycle receipt is invalid",
    ):
        validate_lifecycle_receipt(
            receipt,
            expected_action="trash",
            expected_request_sha256=receipt["source_request_sha256"],
        )
