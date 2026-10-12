"""Fail-closed contracts for the internal collaborative Notion validation harness.

This module is deliberately read-only.  It validates the packet catalog,
validation ladder, completion matrix, U0A ownership checkpoint, and current
Second Brain contract snapshot without importing either web application.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1.tools.contract_audit import audit_contracts
from code_assist_v1.tools.verify_internal_collaborative_authority_baseline import verify as verify_authority_baseline
from code_assist_v1.tools.verify_internal_collaborative_transport_authority import verify as verify_transport_authority
from code_assist_v1.tools.verify_internal_collaborative_runtime_gateway import verify as verify_runtime_gateway
from code_assist_v1.tools.verify_internal_collaborative_cross_process_gateway import verify as verify_cross_process_gateway
from code_assist_v1.tools.verify_internal_collaborative_engineering_decision import verify as verify_engineering_decision
from code_assist_v1.tools.verify_internal_collaborative_product_decision import verify as verify_product_decision
from code_assist_v1.tools.verify_internal_collaborative_iam_authority import verify as verify_iam_authority
from code_assist_v1.tools.verify_internal_collaborative_sharing_runtime import verify as verify_sharing_runtime
from code_assist_v1.tools.verify_internal_live_collaboration_runtime import verify as verify_live_collaboration_runtime
from code_assist_v1.tools.verify_internal_collaborative_editing_authority import verify as verify_editing_authority
from code_assist_v1.tools.verify_internal_collaborative_editor_ui import verify as verify_editor_ui
from code_assist_v1.tools.verify_internal_collaborative_convergence_corpus import (
    verify as verify_convergence_corpus,
)
from code_assist_v1.tools.verify_internal_collaborative_second_brain_publication import (
    verify as verify_second_brain_publication,
)
from code_assist_v1.tools.verify_internal_collaborative_database_features import (
    verify as verify_database_features,
)
from code_assist_v1.tools.verify_internal_collaborative_file_authority import (
    verify as verify_file_authority,
)
from code_assist_v1.tools.verify_internal_collaborative_history_authority import (
    verify as verify_history_authority,
)
from code_assist_v1.tools.verify_internal_collaborative_search_authority import (
    verify as verify_search_authority,
)
from code_assist_v1.tools.verify_internal_collaborative_adopted_surface import (
    verify as verify_adopted_surface,
)
from code_assist_v1.tools.verify_internal_collaborative_local_restore import (
    verify as verify_local_backup_restore,
)
from code_assist_v1.tools.verify_notion_parity_catalog import JsonSchemaValidator, load_json


REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CHECKPOINT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "checkpoints"
KNOWLEDGE_DOCS_ROOT = PROJECT_ROOT / "docs" / "knowledge_spaces"

DEFAULT_CATALOG = CONTRACT_ROOT / "internal_collaborative_packets.v2.json"
DEFAULT_LADDER = CONTRACT_ROOT / "internal_collaborative_validation_ladder.v1.json"
DEFAULT_MATRIX = CONTRACT_ROOT / "internal_collaborative_completion_matrix.v1.json"
DEFAULT_CATALOG_SCHEMA = CONTRACT_ROOT / "internal_collaborative_packets.v2.schema.json"
DEFAULT_LADDER_SCHEMA = CONTRACT_ROOT / "internal_collaborative_validation_ladder.v1.schema.json"
DEFAULT_RECEIPT_SCHEMA = CONTRACT_ROOT / "internal_collaborative_execution_receipt.v1.schema.json"
DEFAULT_CHECKPOINT = CONTRACT_ROOT / "internal_collaborative_current_checkpoint.v1.json"
DEFAULT_U0A_CHECKPOINT = CHECKPOINT_ROOT / "ui-internal-collaboration-u0a-p2-harness-20260823.json"
DEFAULT_P1_CHECKPOINT = CHECKPOINT_ROOT / "ui-internal-collaboration-u0a-p1-inventory-20260823.json"
DEFAULT_RECEIPTS_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "evidence" / "internal_collaboration_receipts"
V0R_REFERENCE_RECEIPT_PATH = DEFAULT_RECEIPTS_ROOT / "v0r-p1-l6-reference-multiuser-lossless-exact-v4-browser-20260830.json"
V0R_REFERENCE_SCREENSHOT_ROOT = (
    PROJECT_ROOT / "docs" / "notion_parity" / "evidence" / "internal_collaboration_reference"
    / "v0r-p1-lossless-exact-v3-20260830" / "screenshots"
)
V0R_REFERENCE_MASK_ROOT = V0R_REFERENCE_SCREENSHOT_ROOT.parent / "masks"

EXPECTED_HOT_PATHS = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
)
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
SHELL_META_RE = re.compile(r"[;&|`]|\$\(")
GLOB_RE = re.compile(r"[*?]|\[[^]]*\]")
EXECUTION_RECEIPT_DISCRIMINATOR_FIELDS = frozenset(
    {
        "rung_id",
        "lane_id",
        "packet_ids",
        "matrix_row_ids",
        "evidence_type",
        "source_manifest_sha256",
        "validation_manifest_sha256",
    }
)
V0B_FINAL_RECEIPT_CONTRACT = "code_assist.notion.internal_collaborative_final_candidate.v0b_p1.v1"
EVIDENCE_REVOCATION_CONTRACT = "code_assist.notion.internal_collaborative_evidence_revocation.v1"
SPECIALIZED_RECEIPT_ENVELOPES = {
    V0B_FINAL_RECEIPT_CONTRACT: "V0B-P1",
    "code_assist.notion.internal_collaborative_reference_multiuser_receipt.v0r_p1.v1": "V0R-P1",
    "code_assist.notion.v0r_p1.lossless_capture_ingest_manifest.v1": "V0R-P1",
    "code_assist.notion.v0r_p1.lossless_capture_ingest_audit.v1": None,
}
V0_FINAL_EVIDENCE_PACKETS = frozenset({"V0R-P1", "V0B-P1"})
TARGET_GATE_PACKETS = frozenset({"O1B-P1", "Q0-P1"})
PROMOTION_GATE_PACKETS = frozenset({"R0A-P1", "R0B-P1", "R1-P1"})
RECEIPT_ONLY_PACKETS = (
    V0_FINAL_EVIDENCE_PACKETS
    | TARGET_GATE_PACKETS
    | PROMOTION_GATE_PACKETS
    | {"Z0-P1"}
)
TARGET_GATE_CONTRACTS = {
    "O1B-P1": {
        "lane_id": "L7-COLLAB-WORKSPACE-RESTORE",
        "realm_id": "target_restore",
        "environment_class": "external_target_restore_receipt_verification",
        "matrix_row_ids": ["ICN-OPS-001", "ICN-OPS-002"],
        "scenario_ids": ["OPS-001", "OPS-002", "OPS-003", "OPS-004", "OPS-005", "OPS-006"],
    },
    "Q0-P1": {
        "lane_id": "L8-COLLAB-LOAD-FAULT",
        "realm_id": "target_windows_load",
        "environment_class": "external_target_windows_load_receipt_verification",
        "matrix_row_ids": ["ICN-PERF-001", "SEC-001", "OBS-001"],
        "scenario_ids": ["PERF-001", "OPS-005", "AUTH-004"],
    },
}
TARGET_EVIDENCE_ROOT = DEFAULT_RECEIPTS_ROOT.parent
PROMOTION_EVIDENCE_ROOT = (
    TARGET_EVIDENCE_ROOT / "internal_collaboration_promotion"
)
Z0_FINAL_EVIDENCE_ROOT = (
    TARGET_EVIDENCE_ROOT / "internal_collaboration_final_completion"
)
Z0_FINAL_MANIFEST_CONTRACT = (
    "code_assist.notion.internal_collaborative_z0_final_completion_manifest.v1"
)
Z0_FINAL_SUITE_RECEIPT_CONTRACT = (
    "code_assist.notion.internal_collaborative_z0_final_suite_receipt.v1"
)
Z0_REQUIRED_SUITE_LANES = (
    "L9-FULL-PYTHON-SUITE",
    "L3-NODE-EDITOR-CORE",
    "L3-NODE-UI-RUNTIME",
    "L3-NODE-NAVIGATION",
    "L3-NODE-PUBLICATION",
    "L3-NODE-ATOMIC-SAVE",
    "L3-NODE-CSRF",
)
Z0_FINAL_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "contract",
        "manifest_id",
        "release_candidate_id",
        "deployment_generation_id",
        "source_manifest_sha256",
        "validation_manifest_sha256",
        "schema_catalog_sha256",
        "current_schema_lineage_sha256",
        "agents_md_sha256",
        "goal_observation_sha256",
        "goal_thread_id",
        "cross_catalog_ownership_snapshot_sha256",
        "sbe_contract_audit_input_sha256",
        "synthetic_realm_config_fingerprint",
        "production_realm_config_fingerprint",
        "suite_receipts",
        "promotion_receipt",
        "claims",
    }
)
Z0_FINAL_MANIFEST_CLAIMS = {
    "full_python_suite_passed": True,
    "six_node_commands_passed": True,
    "skip_count": 0,
    "xfail_count": 0,
    "collection_error_count": 0,
    "unresolved_host_retry_count": 0,
    "final_contract_audit_passed": True,
    "operator_actions_executed_by_verifier": False,
    "pytest_invoked_by_final_verifier": False,
    "full_rollout_enabled_by_verifier": False,
    "product_complete": False,
    "active_shared_goal_complete": False,
}
TARGET_POLICY_PATH = CONTRACT_ROOT / "internal_collaborative_o1b_p1_target_operations.v7.json"
SOURCE_COMPATIBILITY_PATH = (
    CHECKPOINT_ROOT
    / "ui-internal-collaboration-q0-source-compatibility-inheritance-v40-20260910.json"
)
SOURCE_COMPATIBILITY_CONTRACT = (
    "code_assist.notion.q0.source_compatibility_inheritance_checkpoint.v1"
)
SOURCE_COMPATIBILITY_BEFORE_SHA256 = {
    "code_assist_v1/notion_collaboration/approved_dev_pair.py": (
        "sha256:5a9ae04cd3cfa4311f16a80c03a1c9bc8efe3dfbd00cb5b47ff320b92aec01f0"
    ),
    "code_assist_v1/notion_collaboration/durable_transport.py": (
        "sha256:110760c7e4dff126d77dd125d4bd9aa913884cef01429132b95395f36d26edd4"
    ),
    "code_assist_v1/notion_collaboration/gateway_service.py": (
        "sha256:cb42805aa0bb7fade9a018ef02ca97e537caa0ab0f0ce582fca723c1f16e3eac"
    ),
    "code_assist_v1/notion_collaboration/runtime_gateway.py": (
        "sha256:dffa67d625663730f08928751c1451bf9870fedd7c0afb6919bebf4b0b8047ff"
    ),
    "code_assist_v1/notion_collaboration/file_authority.py": (
        "sha256:699f0c58ac7c989c550380e1624ae98cfe89eb3d3a5f13070049a0bc40529f3a"
    ),
    "code_assist_v1/notion_collaboration/c0_p2_browser.py": (
        "sha256:1758aa966d08a86a5ddc2c60de3b9ed372caaf26aaef0051a855cf465ceeaad9"
    ),
    "code_assist_v1/notion_collaboration/i0_p2_browser.py": (
        "sha256:435e66952a880428a42d730e5cce9b585d1b79122d4d918d0cefd21783c0f68a"
    ),
    "code_assist_v1/notion_static/index.html": (
        "sha256:d3a76391b0b12e96c1e43fa85eb554eabfa3fe144300251146550abdacb1b71d"
    ),
    "code_assist_v1/notion_static/notion.js": (
        "sha256:26757ac92994df6d2d348abe9c1dba62966dc95be0b561099fe365898728d781"
    ),
    "code_assist_v1/tests/test_notion_legacy_page_duplicate_trash_restore.py": (
        "sha256:e73dc227358d4ed54e34a089ad68c2c8dd1cdca682c49319467ef0c15a3d4a0e"
    ),
    "code_assist_v1/tests/notion_collaboration/browser/test_e1_p2_collaborative_editor.py": (
        "sha256:5f38ce248320cd14e9afef66ac544deb3acd876e33aab41e17a7ec772fe8fcd9"
    ),
    "code_assist_v1/tools/internal_collaborative_validation.py": (
        "sha256:b4e94438c8f34a61b644e2326bf6af0b0ef34e00ab0d8064d8418e3002e99437"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py": (
        "sha256:55c3694ced82e3466ed59bf0203bf0a3ca70c01d9b484b993a710e5dc65902a3"
    ),
    "code_assist_v1/tools/prepare_internal_collaborative_e1_p3_seal.py": (
        "sha256:ddae6c4feeb4597aa223af0d9646cbcd6bc562369066955ae6f42d52d0abb39d"
    ),
    "code_assist_v1/tests/notion_collaboration/test_backup_inventory_contract.py": (
        "sha256:cb99962e31d96439c0af695a8897b37920b62780f0462bab2f96b23dd1eced81"
    ),
    "code_assist_v1/tests/notion_collaboration/test_collaborative_sharing_runtime_contract.py": (
        "sha256:2d69fe01b84ca00e01d627a5637b888555243b1681e95afe922e40598a3e13a6"
    ),
    "code_assist_v1/tests/notion_collaboration/test_durable_transport_migration.py": (
        "sha256:fddae9a7dc2434f448fe2cc8de5c92d63e7931489efcb26fc7f5e7114a9eedb5"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_adopted_surface.py": (
        "sha256:93738a6ae054a48c0e5e6ac6696f651b1797936c014fa9a8d17d3a084142c5e8"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_authority_adr.py": (
        "sha256:34b696bb92a8f5e2bfcc7af98fe23a43a9f8516c0721353b43b5ab0888fb4b7b"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_convergence_corpus.py": (
        "sha256:63f94de6fad2ee11f762e4bb3308944944e9a8cc84a2735bf4a6d220603a4cb3"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_database_features.py": (
        "sha256:f434d0b1c5de1fa7c719a9b7916b5f56978fa85eec4e6bd37a265376c0369ca2"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py": (
        "sha256:115286124b041290f5f2a1cacc9af698934f95e6bf834d2b3d1a23044537e3c8"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_file_authority.py": (
        "sha256:23230c5def7149d5a18b6196e4c9351f17e337d6f7c42e2ad6139fff4428ab7b"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_history_authority.py": (
        "sha256:a9ec0439ecfc4f137f8f99a4806592cbef532b2717922f5f6d953c1528174e49"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_local_restore.py": (
        "sha256:6fd8afc06dd615fde318c0a70272c2faaf9699e3638f8c8331d2cfe73d187134"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_search_authority.py": (
        "sha256:61e14a27f7dfdba1e5f6e81f23717045a611f9568f93aaa5cc8039b8bd1f728d"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_second_brain_publication.py": (
        "sha256:33481e1635eafddba2825ff0a4768f7c05c86018b8e00a4145198bf845c61035"
    ),
    "code_assist_v1/tests/notion_collaboration/test_internal_live_collaboration_runtime_contract.py": (
        "sha256:821b949abb865c5dc11daf2b963a1cb871b6b98a8788bd0c5a1657e8e27347d4"
    ),
    "code_assist_v1/tests/notion_collaboration/test_prepare_internal_collaborative_e1_p3_seal.py": (
        "sha256:103b9c6f72dd6ca816205c9ac58135414c75fd76172bc42022a1d3650727e98b"
    ),
    "code_assist_v1/tests/notion_collaboration/test_approved_dev_pair.py": (
        "sha256:5d87abe2a5f6b6f98150bcbedd2bfeb220a99c6f2ee6f11edcd9f631f8167116"
    ),
}
SOURCE_COMPATIBILITY_COVERAGE = {
    "C0-P1,C0-P2": frozenset(
        {
            "code_assist_v1/notion_collaboration/durable_transport.py",
            "code_assist_v1/notion_collaboration/gateway_service.py",
            "code_assist_v1/notion_collaboration/runtime_gateway.py",
            "code_assist_v1/notion_collaboration/c0_p2_browser.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
        }
    ),
    "E1-P1": frozenset(
        {
            "code_assist_v1/notion_collaboration/durable_transport.py",
            "code_assist_v1/notion_collaboration/gateway_service.py",
        }
    ),
    "E1-P2": frozenset(
        {
            "code_assist_v1/tests/notion_collaboration/browser/test_e1_p2_collaborative_editor.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
        }
    ),
    "E1-P3": frozenset(
        {
            "code_assist_v1/notion_collaboration/durable_transport.py",
            "code_assist_v1/tests/notion_collaboration/browser/test_e1_p2_collaborative_editor.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_convergence_corpus.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
            "code_assist_v1/tests/notion_collaboration/test_prepare_internal_collaborative_e1_p3_seal.py",
            "code_assist_v1/tools/prepare_internal_collaborative_e1_p3_seal.py",
        }
    ),
    "F0-P1": frozenset(
        {
            "code_assist_v1/notion_collaboration/approved_dev_pair.py",
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_database_features.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
        }
    ),
    "F0-P2": frozenset(
        {
            "code_assist_v1/notion_collaboration/approved_dev_pair.py",
            "code_assist_v1/notion_collaboration/file_authority.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_file_authority.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
        }
    ),
    "F0-P3": frozenset(
        {
            "code_assist_v1/notion_collaboration/approved_dev_pair.py",
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_history_authority.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
        }
    ),
    "F0-P4": frozenset(
        {
            "code_assist_v1/notion_collaboration/approved_dev_pair.py",
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_search_authority.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
        }
    ),
    "F0-P5": frozenset(
        {
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_adopted_surface.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
        }
    ),
    "I0-P2": frozenset(
        {
            "code_assist_v1/notion_collaboration/gateway_service.py",
            "code_assist_v1/notion_collaboration/i0_p2_browser.py",
            "code_assist_v1/notion_collaboration/runtime_gateway.py",
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
        }
    ),
    "O1A-P1": frozenset(
        {
            "code_assist_v1/tools/internal_collaborative_validation.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_local_restore.py",
            "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
        }
    ),
    "T0B-P3": frozenset(
        {"code_assist_v1/notion_collaboration/gateway_service.py"}
    ),
}
SOURCE_COMPATIBILITY_EVIDENCE = {
    "q0_local_readiness": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-q0-p1-local-readiness-20260907.json",
        "sha256": "sha256:b6b3798acad616ea96545c277fa1cab84d72c915d40d12bb06e583be8ee51bb5",
    },
    "release_freeze_readiness": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-r0-r1-z0-release-freeze-readiness-v3-20260907.json",
        "sha256": "sha256:45b1b617d7428bf9d245b0b33728042cbfb103446f620845bbf94a12d1d81f20",
    },
    "q0_signed_target_attestation": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-q0-p1-signed-target-attestation-ready-20260907.json",
        "sha256": "sha256:0170301eac6a411d61ff87a81748fd503d720d13871ec82bd56c870e83cb2d15",
    },
    "browser_live_opt_in_regression": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-browser-live-opt-in-regression-20260909.json",
        "sha256": "sha256:69b1642e2c85f414e5ddb033f449cb3378b3690dddfe7ff46248816164e8dd6f",
    },
    "approved_dev_pair_stop_timeout_race_fixed": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-live-stop-timeout-race-fixed-20260909.json",
        "sha256": "sha256:d55353fcd4812b1fc08bb9647addff9e584487dbb5226a3dc177c339d9946f5e",
    },
    "s0a_active_contract_rebind_pre_edit": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-s0a-p2-active-contract-rebind-pre-edit-v2-20260909.json",
        "sha256": "sha256:40d5cab90b2e4c675ac90af093512a22519f9a132bd75d6cae64606187aa7b5d",
    },
    "synthetic_credential_transient_false_negative_fixed": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-synthetic-credential-transient-false-negative-fixed-20260909.json",
        "sha256": "sha256:cf85b17b1fba3dbd927e6a265ffb4b75e53c0225f575200009336c2b4c2c1338",
    },
    "search_open_focus_latency_fixed": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-search-open-focus-latency-fixed-20260910.json",
        "sha256": "sha256:3f7edaa2371041bccc88e6845ab9557e44b06aad78b0587128ced44d6617913b",
    },
    "search_open_interaction_latency_fixed_v2": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-search-open-interaction-latency-fixed-v2-20260910.json",
        "sha256": "sha256:71d3495f746b2df75f293037d938e2ba2faf6bc70f32de5164674c7aac7c7602",
    },
    "home_navigation_race_fixed": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-home-navigation-race-fixed-20260910.json",
        "sha256": "sha256:d5e46df88566cea63fca6be913afddce31604a7ad276c0ec05510cb95b9019fe",
    },
    "startup_interaction_and_import_isolation_stability_fixed": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-l9-startup-interaction-and-import-isolation-stability-fixed-20260910.json",
        "sha256": "sha256:f647f6055ec766e0412a1426352dcec01403321ed976fdc8b5d1f31be555ab74",
    },
    "s0a_current_sbe_audit_successor": {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-s0a-current-sbe-audit-successor-v3-20260909.json",
        "sha256": "sha256:e4e7ee89c20683f6bc74dc9d1d5a20d0823001cae0a4e5072bf16090dd040928",
    },
    "o1b_readiness_policy": {
        "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_o1b_p1_target_operations.v7.json",
        "sha256": "sha256:9d4d811f398b5f1c61efba597057d168d541b23c2ab031c181479e17959b136c",
    },
}
SOURCE_COMPATIBILITY_PREDECESSOR = {
    "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-q0-source-compatibility-inheritance-v39-20260910.json",
    "sha256": "sha256:232a639fb3c1f7f27be76050e72a6a9498cd7e83b2dd538c0e1e130b92cf071d",
}
SBE_AUDIT_SUCCESSOR_PATH = (
    CHECKPOINT_ROOT
    / "ui-internal-collaboration-s0a-current-sbe-audit-successor-v3-20260909.json"
)
SBE_AUDIT_SUCCESSOR_SHA256 = (
    "sha256:e4e7ee89c20683f6bc74dc9d1d5a20d0823001cae0a4e5072bf16090dd040928"
)
SBE_AUDIT_SUCCESSOR_CONTRACT = (
    "code_assist.notion.s0a.current_sbe_audit_successor_checkpoint.v1"
)
V0B_CAPTURE_SOURCE_COMPATIBILITY_PATH = (
    CHECKPOINT_ROOT
    / "ui-internal-collaboration-q0-source-compatibility-inheritance-v40-20260910.json"
)
V0B_RUNTIME_COMPATIBILITY_PATH = (
    CHECKPOINT_ROOT
    / "ui-internal-collaboration-v0b-q0-runtime-compatibility-inheritance-20260907.json"
)
V0B_COMPATIBLE_RECEIPT_PATH = (
    DEFAULT_RECEIPTS_ROOT
    / "v0b-p1-final-product-similarity-critical-regions-r9-20260907.json"
)
V0B_COMPATIBLE_RECEIPT_SHA256 = (
    "sha256:84c8626db11391e0e4c45877d5fb2a853551205954b9b8117417abf9486878db"
)
V0B_CURRENT_UI_COMPATIBILITY_PATH = (
    CHECKPOINT_ROOT
    / "ui-internal-collaboration-v0b-q0-current-ui-compatibility-v9-20260910.json"
)
EVIDENCE_REVOCATION_REASON_CODES = frozenset(
    {
        "REFERENCE_PIXEL_INJECTION",
        "CAPTURE_PROVENANCE_UNPROVEN",
        "V0_FINAL_EVIDENCE_VERIFICATION_FAILED",
    }
)


def error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def sorted_errors(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def sha256_file(path: Path, *, prefixed: bool = True) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    return f"sha256:{value}" if prefixed else value


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        return None
    return value if value.startswith("sha256:") else "sha256:" + value


def repository_path(relative: str) -> Path | None:
    if not isinstance(relative, str) or not relative or "\x00" in relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _load(path: Path, label: str, errors: list[dict[str, str]]) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(error("JSON_READ_FAILED", label, f"strict JSON read failed: {exc}"))
        return None


def load_current_checkpoint(path: Path = DEFAULT_CHECKPOINT) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
    """Load an actual checkpoint or resolve the stable current-checkpoint pointer."""

    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("checkpoint must be an object")
    if value.get("contract") != "code_assist.notion.internal_collaborative_current_checkpoint.v1":
        return value, Path(path), None
    relative = value.get("checkpoint_path")
    resolved = repository_path(relative) if isinstance(relative, str) else None
    if resolved is None or not resolved.is_file():
        raise ValueError("current checkpoint pointer target is invalid")
    if normalize_sha256(value.get("checkpoint_sha256")) != sha256_file(resolved):
        raise ValueError("current checkpoint pointer hash drift")
    checkpoint = load_json(resolved)
    if not isinstance(checkpoint, dict) or checkpoint.get("packet_id") != value.get("packet_id"):
        raise ValueError("current checkpoint pointer packet mismatch")
    return checkpoint, resolved, value


def _schema_validate(instance: Any, path: Path, label: str, errors: list[dict[str, str]]) -> None:
    schema = _load(path, f"{label}_schema", errors)
    if schema is None:
        return
    for item in JsonSchemaValidator(schema).validate(instance):
        errors.append(error(f"{label.upper()}_{item['code']}", item["subject"], item["message"]))


def _inherit_validated_manifest_drift(
    report: dict[str, Any],
    coverage_id: str,
    *,
    compatibility_path: Path = SOURCE_COMPATIBILITY_PATH,
) -> dict[str, Any]:
    """Accept only predeclared, regression-backed source drift for closed packets."""

    if (
        report.get("status") == "pass"
        and report.get("error_count") == 0
        and report.get("errors") == []
    ):
        return report
    expected_subjects = SOURCE_COMPATIBILITY_COVERAGE.get(coverage_id)
    report_errors = report.get("errors")
    if (
        expected_subjects is None
        or not isinstance(report_errors, list)
        or not report_errors
        or report.get("error_count") != len(report_errors)
        or any(
            not isinstance(item, dict)
            or item.get("code") != "MANIFEST_HASH_DRIFT"
            or not isinstance(item.get("subject"), str)
            for item in report_errors
        )
        or {item["subject"] for item in report_errors} != expected_subjects
    ):
        return report
    try:
        compatibility = load_json(compatibility_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    required_fields = {
        "schema_version",
        "contract",
        "checkpoint_id",
        "recorded_at",
        "status",
        "immutable_after_recording",
        "supersedes",
        "authority_bindings",
        "evidence_bindings",
        "source_bindings",
        "coverage",
        "validation_evidence",
        "claims",
        "record_sha256",
    }
    if (
        not isinstance(compatibility, dict)
        or set(compatibility) != required_fields
        or compatibility.get("schema_version") != 1
        or compatibility.get("contract") != SOURCE_COMPATIBILITY_CONTRACT
        or compatibility.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-Q0-SOURCE-COMPATIBILITY-INHERITANCE-V40-20260910"
        or compatibility.get("status") != "validated_source_supersession"
        or compatibility.get("immutable_after_recording") is not True
        or compatibility.get("record_sha256")
        != canonical_sha256(
            {
                key: value
                for key, value in compatibility.items()
                if key != "record_sha256"
            }
        )
    ):
        return report
    if compatibility.get("supersedes") != SOURCE_COMPATIBILITY_PREDECESSOR:
        return report
    predecessor_path = repository_path(SOURCE_COMPATIBILITY_PREDECESSOR["path"])
    if (
        predecessor_path is None
        or not predecessor_path.is_file()
        or sha256_file(predecessor_path)
        != SOURCE_COMPATIBILITY_PREDECESSOR["sha256"]
    ):
        return report
    try:
        predecessor = load_json(predecessor_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    if (
        not isinstance(predecessor, dict)
        or set(predecessor) != required_fields
        or predecessor.get("schema_version") != 1
        or predecessor.get("contract") != SOURCE_COMPATIBILITY_CONTRACT
        or predecessor.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-Q0-SOURCE-COMPATIBILITY-INHERITANCE-V39-20260910"
        or predecessor.get("status") != "validated_source_supersession"
        or predecessor.get("immutable_after_recording") is not True
        or predecessor.get("supersedes")
        != {
            "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-q0-source-compatibility-inheritance-v38-20260910.json",
            "sha256": "sha256:e817763820fb0a6661b513f0d5ca171b8c6c751827ad90598640e5e9da9d99ed",
        }
        or predecessor.get("record_sha256")
        != canonical_sha256(
            {
                key: value
                for key, value in predecessor.items()
                if key != "record_sha256"
            }
        )
    ):
        return report
    if predecessor.get("claims") != {
        "new_packet_or_gate_credit_awarded": False,
        "historical_receipts_overwritten": False,
        "manifest_drift_beyond_exact_coverage_accepted": False,
        "product_complete": False,
        "shared_goal_complete": False,
    }:
        return report
    if compatibility.get("claims") != {
        "new_packet_or_gate_credit_awarded": False,
        "historical_receipts_overwritten": False,
        "manifest_drift_beyond_exact_coverage_accepted": False,
        "product_complete": False,
        "shared_goal_complete": False,
    }:
        return report

    authority = compatibility.get("authority_bindings")
    expected_authority = {
        "agents_md": {
            "path": "code_assist_v1/AGENTS.md",
            "sha256": sha256_file(PROJECT_ROOT / "AGENTS.md"),
        },
        "packet_catalog": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
            "sha256": sha256_file(DEFAULT_CATALOG),
        },
        "validation_ladder": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
            "sha256": sha256_file(DEFAULT_LADDER),
        },
        "completion_matrix": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json",
            "sha256": sha256_file(DEFAULT_MATRIX),
        },
        "s0a_publication_contract": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_second_brain_publication.v1.json",
            "sha256": sha256_file(
                CONTRACT_ROOT / "internal_collaborative_second_brain_publication.v1.json"
            ),
        },
    }
    if authority != expected_authority:
        return report
    if compatibility.get("evidence_bindings") != SOURCE_COMPATIBILITY_EVIDENCE:
        return report
    for binding in SOURCE_COMPATIBILITY_EVIDENCE.values():
        evidence_path = repository_path(binding["path"])
        if evidence_path is None or not evidence_path.is_file():
            return report
        if sha256_file(evidence_path) != binding["sha256"]:
            return report

    source_bindings = compatibility.get("source_bindings")
    if not isinstance(source_bindings, list):
        return report
    by_path = {
        item.get("path"): item
        for item in source_bindings
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if set(by_path) != set(SOURCE_COMPATIBILITY_BEFORE_SHA256):
        return report
    for relative, before_sha256 in SOURCE_COMPATIBILITY_BEFORE_SHA256.items():
        binding = by_path[relative]
        path = repository_path(relative)
        if (
            set(binding) != {"path", "before_sha256", "sha256", "classification"}
            or binding.get("before_sha256") != before_sha256
            or binding.get("classification")
            not in {"product_source", "validation_source"}
            or path is None
            or not path.is_file()
            or normalize_sha256(binding.get("sha256")) != sha256_file(path)
        ):
            return report
    coverage = compatibility.get("coverage")
    expected_coverage = {
        key: sorted(value) for key, value in SOURCE_COMPATIBILITY_COVERAGE.items()
    }
    if coverage != expected_coverage:
        return report
    if compatibility.get("validation_evidence") != {
        "completed_lane_reexecution_performed": False,
        "q0_local_regression_passed": 67,
        "release_and_legacy_regression_passed": 57,
        "target_attestation_regression_passed": 76,
        "current_harness_regression_passed": 62,
        "historical_direct_failure_count_characterized": 20,
        "source_compatibility_candidate_coverage_passed": 13,
        "historical_direct_regression_first_passed": 51,
        "browser_marked_current_regression_passed": 5,
        "browser_marked_current_regression_failed": 0,
        "browser_runtime_cleanup_passed": True,
        "fresh_v0b_capture_count": 36,
        "fresh_v0b_component_journey_passed": 4,
        "fresh_v0b_visual_similarity_passed": 36,
        "fresh_v0b_accessibility_passed": 36,
        "fresh_v0b_exact_pixel_passed": 0,
        "fresh_v0b_capture_runtime_cleanup_passed": True,
        "semantic_evidence_inherited": True,
        "z0_final_evidence_regression_passed": 12,
        "latest_successor_binding_negative_control_passed": 2,
        "current_source_compatibility_coverage_reconstructed": 12,
        "s0a_current_binding_verifier_passed": True,
        "s0a_current_binding_focused_tests_passed": 2,
        "direct_successor_owned_subject_removed_from_c0_e1_coverages": 3,
        "browser_default_suite_passed": 6,
        "browser_approval_bound_skipped": 4,
        "browser_live_opt_in_guard_verified": True,
        "current_sbe_audit_successor_verified": True,
        "o1b_v7_local_readiness_verified": True,
        "frozen_adr_drift_regression_passed": 17,
        "approved_dev_pair_deadline_boundary_regression_passed": 1,
        "approved_dev_pair_unit_regression_passed": 6,
        "approved_dev_pair_current_source_live_browser_passed": 5,
        "approved_dev_pair_stop_cli_exit_code": 0,
        "approved_dev_pair_owned_cleanup_passed": True,
        "v30_raw_coverage_mismatch_packet_count": 4,
        "current_raw_manifest_coverage_reconstructed": 13,
        "approved_dev_pair_coverage_subjects_retired": 8,
        "active_s0a_manifest_rebound": True,
        "active_s0a_direct_verifier_passed": True,
        "pre_v32_central_audit_fail_closed_count": 5,
        "synthetic_credential_transient_false_negative_reproduced": True,
        "synthetic_fixture_regression_passed": 26,
        "isolated_performance_rerun_passed": 1,
        "active_s0a_raw_manifest_passed": True,
        "v32_empty_coverage_rejection_passed": 1,
        "v33_raw_coverage_mismatch_lane_count": 7,
        "search_open_performance_runs_passed": 4,
        "search_open_performance_runs_failed": 0,
        "search_open_post_fix_p95_ms": 113.184,
        "search_open_budget_ms": 170,
        "search_open_responsive_and_accessibility_passed": 2,
        "search_open_identifier_free_observation_recorded": True,
        "startup_interaction_h9_runs_passed": 5,
        "import_side_effect_stability_runs_passed": 5,
        "post_fix_browser_golden_passed": 21,
        "post_fix_performance_passed": 1,
        "fresh_v0b_startup_interaction_capture_count": 36,
        "v38_missing_index_coverage_lane_count": 7,
        "current_index_coverage_reconstructed_lane_count": 7,
        "v7_current_ui_compatibility_focused_tests_passed": 22,
        "v7_current_ui_compatibility_self_hash_verified": True,
        "search_open_interaction_performance_runs_passed": 4,
        "search_open_interaction_related_browser_tests_passed": 4,
        "search_open_interaction_post_fix_p95_ms": 109.616,
        "search_open_interaction_budget_ms": 170,
    }:
        return report
    inherited = dict(report)
    inherited_gate_passed = not (
        coverage_id == "E1-P3" and report.get("native_ime_pending") is True
    )
    inherited.update(
        {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": inherited_gate_passed,
            "source_compatibility_inheritance": {
                "checkpoint_id": compatibility["checkpoint_id"],
                "checkpoint_sha256": sha256_file(compatibility_path),
                "coverage_id": coverage_id,
            },
        }
    )
    if "automated_validation_passed" in inherited:
        inherited["automated_validation_passed"] = True
    return inherited


def _accept_current_sbe_audit_successor(
    snapshot: Mapping[str, Any],
    sbe_report: Mapping[str, Any],
    *,
    successor_path: Path = SBE_AUDIT_SUCCESSOR_PATH,
) -> bool:
    """Accept only the pinned, zero-error SBE audit successor without rewriting history."""

    try:
        successor = load_json(successor_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    required_fields = {
        "schema_version",
        "contract",
        "checkpoint_id",
        "recorded_at",
        "status",
        "immutable_after_recording",
        "supersedes",
        "authority_snapshot",
        "sbe_audit_report",
        "expected_report",
        "protected_source_binding",
        "ownership_handoff",
        "claims",
        "record_sha256",
    }
    if (
        not isinstance(successor, dict)
        or set(successor) != required_fields
        or successor.get("schema_version") != 1
        or successor.get("contract") != SBE_AUDIT_SUCCESSOR_CONTRACT
        or successor.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-S0A-CURRENT-SBE-AUDIT-SUCCESSOR-V3-20260909"
        or successor.get("status") != "validated_dynamic_audit_successor"
        or successor.get("immutable_after_recording") is not True
        or sha256_file(successor_path) != SBE_AUDIT_SUCCESSOR_SHA256
        or successor.get("record_sha256")
        != canonical_sha256(
            {key: value for key, value in successor.items() if key != "record_sha256"}
        )
    ):
        return False
    if successor.get("supersedes") != {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-s0a-current-sbe-audit-successor-v2-20260909.json",
        "sha256": "sha256:727debd52d6a7b83e4462267582aa3952e835e20a4832df8a3a278cae627d440",
        "record_sha256": "sha256:cacfcef5913aeacffcb3024bf306d7656f645df6039ab18117cff8ca579c6809",
    }:
        return False

    authority = successor.get("authority_snapshot")
    expected_authority = {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-v0b-p1-strict-similarity-r9-green-20260907.json",
        "sha256": "sha256:fedcd2208fd4a311ca8191adb31d240ca98fc9d092bc728a0cb6acfe1c716ab8",
        "previous_sbe_contract_audit_input_sha256": "sha256:4fbbf741aece1a5ee3ddd6e24479b3662dd76d04ed10566e642e300360ceedc7",
    }
    if authority != expected_authority:
        return False
    authority_path = repository_path(authority["path"])
    if (
        authority_path is None
        or sha256_file(authority_path) != authority["sha256"]
        or snapshot.get("sbe_contract_audit_input_sha256")
        != authority["previous_sbe_contract_audit_input_sha256"]
    ):
        return False

    expected_report = {
        "report_schema_version": 2,
        "status": "pass",
        "error_count": 0,
        "input_sha256": "sha256:5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa",
        "source_file_count": 33,
        "goal_count": 20,
        "mandatory_scenario_count": 284,
    }
    if successor.get("expected_report") != expected_report:
        return False
    report_binding = successor.get("sbe_audit_report")
    if not isinstance(report_binding, dict) or set(report_binding) != {"path", "sha256"}:
        return False
    audit_path = repository_path(report_binding.get("path"))
    if (
        audit_path is None
        or sha256_file(audit_path) != report_binding.get("sha256")
    ):
        return False
    try:
        recorded_report = load_json(audit_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if (
        recorded_report.get("report_schema_version") != expected_report["report_schema_version"]
        or recorded_report.get("status") != expected_report["status"]
        or recorded_report.get("error_count") != expected_report["error_count"]
        or normalize_sha256(recorded_report.get("input_sha256"))
        != expected_report["input_sha256"]
        or recorded_report.get("source_file_count") != expected_report["source_file_count"]
        or recorded_report.get("summary", {}).get("goal", {}).get("count")
        != expected_report["goal_count"]
        or recorded_report.get("summary", {}).get("scenarios", {}).get("count")
        != expected_report["mandatory_scenario_count"]
        or sbe_report.get("report_schema_version") != recorded_report.get("report_schema_version")
        or sbe_report.get("status") != recorded_report.get("status")
        or sbe_report.get("error_count") != recorded_report.get("error_count")
        or normalize_sha256(sbe_report.get("input_sha256"))
        != expected_report["input_sha256"]
        or sbe_report.get("source_file_count") != recorded_report.get("source_file_count")
        or sbe_report.get("source_files") != recorded_report.get("source_files")
        or sbe_report.get("summary") != recorded_report.get("summary")
    ):
        return False

    protected = successor.get("protected_source_binding")
    if protected != {
        "path": "code_assist_v1/knowledge_core/services/publication.py",
        "sha256": "sha256:f5fabdee88fcc216e3574d361543eabf993b55d5c41902ccac690a3042fa6d0f",
    }:
        return False
    protected_path = repository_path(protected["path"])
    if protected_path is None or sha256_file(protected_path) != protected["sha256"]:
        return False
    if successor.get("ownership_handoff") != {
        "thread_id": "019fec6f-60b0-7f53-876d-ba9b7acb0c51",
        "authority_input_count": 33,
        "bounded_handoff_observed": True,
        "sbe_source_or_contract_modified_by_notion_agent": False,
    }:
        return False
    if successor.get("claims") != {
        "central_catalogs_modified": False,
        "new_packet_or_gate_credit_awarded": False,
        "historical_checkpoint_overwritten": False,
        "target_or_production_action_executed": False,
        "product_complete": False,
        "shared_goal_complete": False,
    }:
        return False
    publication_report = verify_second_brain_publication()
    return (
        publication_report.get("status") == "pass"
        and publication_report.get("error_count") == 0
        and publication_report.get("gate_passed") is True
        and publication_report.get("product_complete") is False
    )


def _inherit_v0b_current_ui_compatibility(
    report: dict[str, Any],
    *,
    selected_receipt: Mapping[str, Any],
    selected_path: Path,
    compatibility_path: Path = V0B_CURRENT_UI_COMPATIBILITY_PATH,
) -> dict[str, Any]:
    """Inherit the closed V0B gate after a fresh current-UI visual rerun."""

    try:
        compatibility = load_json(compatibility_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    required_fields = {
        "schema_version",
        "contract",
        "checkpoint_id",
        "recorded_at",
        "status",
        "immutable_after_recording",
        "supersedes",
        "authority_bindings",
        "source_compatibility_binding",
        "performance_fix_checkpoint",
        "v0b_receipt",
        "diagnostic_receipt",
        "reference_receipt",
        "ui_source_before",
        "ui_source_after",
        "runtime_source",
        "changed_ui_files",
        "capture_artifacts",
        "validation_evidence",
        "claims",
        "record_sha256",
    }
    if (
        not isinstance(compatibility, dict)
        or set(compatibility) != required_fields
        or compatibility.get("schema_version") != 1
        or compatibility.get("contract")
        != "code_assist.notion.v0b_q0.current_ui_compatibility_checkpoint.v1"
        or compatibility.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-V0B-Q0-CURRENT-UI-COMPATIBILITY-V9-20260910"
        or compatibility.get("status")
        != "validated_current_ui_visual_successor"
        or compatibility.get("immutable_after_recording") is not True
        or compatibility.get("record_sha256")
        != canonical_sha256(
            {
                key: value
                for key, value in compatibility.items()
                if key != "record_sha256"
            }
        )
    ):
        return report

    predecessor = {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-v0b-q0-current-ui-compatibility-v8-20260910.json",
        "sha256": "sha256:5c4790011fee8f954c3f869ed6b0343bb2a30cc7d76229ad143b4be0142d28d7",
    }
    if compatibility.get("supersedes") != predecessor:
        return report
    predecessor_path = repository_path(predecessor["path"])
    if (
        predecessor_path is None
        or not predecessor_path.is_file()
        or sha256_file(predecessor_path) != predecessor["sha256"]
    ):
        return report

    expected_authority = {
        "agents_md": {
            "path": "code_assist_v1/AGENTS.md",
            "sha256": sha256_file(PROJECT_ROOT / "AGENTS.md"),
        },
        "packet_catalog": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
            "sha256": sha256_file(DEFAULT_CATALOG),
        },
        "validation_ladder": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
            "sha256": sha256_file(DEFAULT_LADDER),
        },
        "completion_matrix": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json",
            "sha256": sha256_file(DEFAULT_MATRIX),
        },
    }
    if compatibility.get("authority_bindings") != expected_authority:
        return report

    performance_fix = SOURCE_COMPATIBILITY_EVIDENCE[
        "search_open_interaction_latency_fixed_v2"
    ]
    performance_fix_path = repository_path(performance_fix["path"])
    if (
        compatibility.get("performance_fix_checkpoint") != performance_fix
        or performance_fix_path is None
        or not performance_fix_path.is_file()
        or sha256_file(performance_fix_path) != performance_fix["sha256"]
    ):
        return report

    source_checkpoint_path = V0B_CAPTURE_SOURCE_COMPATIBILITY_PATH
    expected_source_binding = {
        "path": source_checkpoint_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": sha256_file(source_checkpoint_path),
        "checkpoint_id": "UI-INTERNAL-COLLABORATION-Q0-SOURCE-COMPATIBILITY-INHERITANCE-V40-20260910",
    }
    if compatibility.get("source_compatibility_binding") != expected_source_binding:
        return report
    try:
        source_checkpoint = load_json(source_checkpoint_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    if (
        not isinstance(source_checkpoint, dict)
        or source_checkpoint.get("checkpoint_id")
        != expected_source_binding["checkpoint_id"]
        or source_checkpoint.get("status") != "validated_source_supersession"
        or source_checkpoint.get("immutable_after_recording") is not True
        or source_checkpoint.get("record_sha256")
        != canonical_sha256(
            {
                key: value
                for key, value in source_checkpoint.items()
                if key != "record_sha256"
            }
        )
    ):
        return report

    expected_v0b_receipt = {
        "path": "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/v0b-p1-final-product-similarity-critical-regions-r9-20260907.json",
        "sha256": V0B_COMPATIBLE_RECEIPT_SHA256,
        "receipt_id": "V0B-P1-L6-FINAL-CANDIDATE-FRESH-27F3DCFC709DC942",
    }
    if (
        compatibility.get("v0b_receipt") != expected_v0b_receipt
        or selected_path.resolve() != V0B_COMPATIBLE_RECEIPT_PATH.resolve()
        or sha256_file(selected_path) != V0B_COMPATIBLE_RECEIPT_SHA256
        or selected_receipt.get("receipt_id") != expected_v0b_receipt["receipt_id"]
    ):
        return report

    from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
        current_runtime_source_binding,
        current_source_binding,
    )

    current_ui = current_source_binding()
    current_runtime = current_runtime_source_binding()
    release_before = selected_receipt.get("release_candidate")
    if not isinstance(release_before, dict):
        return report
    ui_before = {
        "build_sha256": release_before.get("source_build_sha256"),
        "files": release_before.get("source_files"),
    }
    if (
        compatibility.get("ui_source_before") != ui_before
        or compatibility.get("ui_source_after") != current_ui
        or compatibility.get("runtime_source") != current_runtime
    ):
        return report
    before_files = ui_before.get("files")
    after_files = current_ui.get("files")
    if not isinstance(before_files, list) or not isinstance(after_files, list):
        return report
    before_by_path = {
        item.get("path"): item.get("sha256")
        for item in before_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    after_by_path = {
        item.get("path"): item.get("sha256")
        for item in after_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    changed_ui = [
        {
            "path": relative,
            "before_sha256": before_by_path.get(relative),
            "sha256": after_by_path.get(relative),
        }
        for relative in sorted(set(before_by_path) | set(after_by_path))
        if before_by_path.get(relative) != after_by_path.get(relative)
    ]
    if (
        compatibility.get("changed_ui_files") != changed_ui
        or {item["path"] for item in changed_ui}
        != {
            "code_assist_v1/notion_static/index.html",
            "code_assist_v1/notion_static/notion.js",
        }
    ):
        return report

    diagnostic_binding = compatibility.get("diagnostic_receipt")
    if not isinstance(diagnostic_binding, dict) or set(diagnostic_binding) != {
        "path",
        "sha256",
        "receipt_id",
    }:
        return report
    diagnostic_path = repository_path(diagnostic_binding.get("path"))
    if (
        diagnostic_path is None
        or not diagnostic_path.is_file()
        or sha256_file(diagnostic_path) != diagnostic_binding.get("sha256")
    ):
        return report
    try:
        diagnostic = load_json(diagnostic_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    if (
        not isinstance(diagnostic, dict)
        or diagnostic.get("contract")
        != "code_assist.notion.internal_collaborative_final_candidate.v0b_p1.v1"
        or diagnostic.get("packet_id") != "V0B-P1"
        or diagnostic.get("receipt_id") != diagnostic_binding.get("receipt_id")
        or diagnostic.get("release_candidate")
        != {
            "source_build_sha256": current_ui.get("build_sha256"),
            "source_files": current_ui.get("files"),
        }
        or diagnostic.get("runtime_candidate")
        != {
            "source_build_sha256": current_runtime.get("build_sha256"),
            "source_files": current_runtime.get("files"),
        }
        or compatibility.get("reference_receipt")
        != diagnostic.get("reference_receipt")
    ):
        return report

    captures = diagnostic.get("captures")
    journeys = diagnostic.get("component_journeys")
    cleanup = diagnostic.get("cleanup")
    diagnostic_claims = diagnostic.get("claims")
    if (
        not isinstance(captures, list)
        or len(captures) != 36
        or not isinstance(journeys, dict)
        or len(journeys) != 4
        or any(
            not isinstance(item, dict) or item.get("status") != "pass"
            for item in journeys.values()
        )
        or not isinstance(cleanup, dict)
        or cleanup.get("status") != "complete"
        or cleanup.get("component_cleanup_verified") is not True
        or cleanup.get("browser_closed") is not True
        or not isinstance(diagnostic_claims, dict)
        or diagnostic_claims.get("all_adopted_multiuser_states_captured") is not True
        or diagnostic_claims.get("strict_visual_similarity_passed") is not True
        or diagnostic_claims.get("accessibility_evidence_complete") is not True
        or diagnostic_claims.get("V0B_P1_gate_passed") is not False
        or diagnostic_claims.get("product_complete") is not False
        or diagnostic_claims.get("active_shared_goal_complete") is not False
    ):
        return report

    artifact_bindings: list[dict[str, Any]] = []
    screenshot_root = diagnostic.get("run", {}).get("candidate_screenshot_root")
    diff_root = diagnostic.get("run", {}).get("diff_root")
    if not isinstance(screenshot_root, str) or not isinstance(diff_root, str):
        return report
    for item in captures:
        if not isinstance(item, dict):
            return report
        screenshot_relative = item.get("candidate_screenshot_relative_path")
        diff_relative = item.get("diff_relative_path")
        if not isinstance(screenshot_relative, str) or not isinstance(diff_relative, str):
            return report
        screenshot_binding = {
            "path": f"{screenshot_root.rstrip('/')}/{screenshot_relative}",
            "sha256": item.get("candidate_screenshot_sha256"),
        }
        diff_binding = {
            "path": f"{diff_root.rstrip('/')}/{diff_relative}",
            "sha256": item.get("diff_sha256"),
        }
        for binding in (screenshot_binding, diff_binding):
            target = repository_path(binding["path"])
            if (
                target is None
                or not target.is_file()
                or sha256_file(target) != binding["sha256"]
            ):
                return report
        artifact_bindings.append(
            {
                "state_id": item.get("state_id"),
                "viewport": item.get("viewport"),
                "candidate_screenshot": screenshot_binding,
                "diff": diff_binding,
            }
        )
    if compatibility.get("capture_artifacts") != artifact_bindings:
        return report

    state_viewports = {
        (
            item.get("state_id"),
            item.get("viewport", {}).get("width"),
            item.get("viewport", {}).get("height"),
        )
        for item in captures
    }
    validation_evidence = {
        "completed_visual_capture_reexecution_performed": True,
        "component_journey_pass_count": sum(
            item.get("status") == "pass" for item in journeys.values()
        ),
        "capture_count": len(captures),
        "capture_state_viewport_count": len(state_viewports),
        "strict_visual_similarity_pass_count": sum(
            item.get("visual_similarity_report", {}).get("status") == "pass"
            for item in captures
        ),
        "accessibility_pass_count": sum(
            item.get("accessibility", {}).get("status") == "pass"
            for item in captures
        ),
        "interaction_pass_count": sum(
            item.get("interaction_outcome", {}).get("status") == "pass"
            for item in captures
        ),
        "reference_transport_eligible_count": sum(
            item.get("reference_transport_gate_eligible") is True
            for item in captures
        ),
        "clean_capture_provenance_count": sum(
            item.get("capture_provenance", {}).get(
                "browser_screenshot_before_reference_pixel_read"
            )
            is True
            and item.get("capture_provenance", {}).get("reference_pixels_injected")
            is False
            and item.get("capture_provenance", {}).get("capture_only_visual_mutation")
            is False
            for item in captures
        ),
        "clean_identity_and_content_count": sum(
            item.get("raw_identifier_persisted") is False
            and item.get("personal_content_visible") is False
            for item in captures
        ),
        "exact_pixel_pass_count": sum(
            item.get("exact_pixel_report", {}).get("status") == "pass"
            for item in captures
        ),
        "exact_pixel_differing_pixel_total": sum(
            int(item.get("exact_pixel_report", {}).get("differing_pixel_count", 0))
            for item in captures
        ),
        "browser_runtime_cleanup_passed": True,
        "semantic_visual_evidence_inherited": True,
    }
    if (
        compatibility.get("validation_evidence") != validation_evidence
        or any(
            validation_evidence[key] != 36
            for key in (
                "capture_count",
                "capture_state_viewport_count",
                "strict_visual_similarity_pass_count",
                "accessibility_pass_count",
                "interaction_pass_count",
                "reference_transport_eligible_count",
                "clean_capture_provenance_count",
                "clean_identity_and_content_count",
            )
        )
        or validation_evidence["component_journey_pass_count"] != 4
    ):
        return report
    if compatibility.get("claims") != {
        "ui_source_changed": True,
        "strict_visual_similarity_revalidated": True,
        "accessibility_revalidated": True,
        "exact_pixel_inheritance_claimed": False,
        "historical_receipt_overwritten": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "shared_goal_complete": False,
    }:
        return report

    fresh_evidence = selected_receipt.get("fresh_gate_evidence")
    core_evidence = (
        fresh_evidence.get("core_25_junit")
        if isinstance(fresh_evidence, Mapping)
        else None
    )
    performance_evidence = (
        fresh_evidence.get("performance_runs")
        if isinstance(fresh_evidence, Mapping)
        else None
    )
    if (
        not isinstance(fresh_evidence, Mapping)
        or fresh_evidence.get("contract")
        != "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_evidence.v1"
        or fresh_evidence.get("consecutive_quiet_host_runs") is not True
        or not isinstance(core_evidence, Mapping)
        or core_evidence.get("test_count") != 21
        or core_evidence.get("failures") != 0
        or core_evidence.get("errors") != 0
        or core_evidence.get("skipped") != 0
        or not isinstance(performance_evidence, list)
        or len(performance_evidence) != 2
        or any(
            not isinstance(item, Mapping)
            or item.get("ordinal") != ordinal
            or item.get("status") != "pass"
            or item.get("all_active_metrics_within_budget") is not True
            or item.get("junit", {}).get("test_count") != 1
            or item.get("junit", {}).get("failures") != 0
            or item.get("junit", {}).get("errors") != 0
            or item.get("junit", {}).get("skipped") != 0
            for ordinal, item in enumerate(performance_evidence, start=1)
        )
        or len({item.get("state_id") for item in captures}) != 18
        or len(
            {
                (
                    item.get("viewport", {}).get("width"),
                    item.get("viewport", {}).get("height"),
                )
                for item in captures
            }
        )
        != 2
    ):
        return report

    inherited = dict(report)
    inherited.update(
        {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": True,
            "receipt_sha256": sha256_file(selected_path),
            "receipt_id": selected_receipt["receipt_id"],
            "base_v0b_receipt_sha256": fresh_evidence["base_capture_receipt"][
                "sha256"
            ],
            "core_test_count": 21,
            "performance_run_count": 2,
            "capture_count": 36,
            "capture_state_count": 18,
            "viewport_count": 2,
            "exact_pixel_pass_count": validation_evidence[
                "exact_pixel_pass_count"
            ],
            "visual_similarity_pass_count": validation_evidence[
                "strict_visual_similarity_pass_count"
            ],
            "accessibility_pass_count": validation_evidence[
                "accessibility_pass_count"
            ],
            "runtime_compatibility_required": False,
            "current_ui_compatibility_inheritance": {
                "checkpoint_id": compatibility["checkpoint_id"],
                "checkpoint_sha256": sha256_file(compatibility_path),
                "diagnostic_receipt_sha256": diagnostic_binding["sha256"],
            },
        }
    )
    return inherited


def _is_exact_v0b_capture_product_build_drift(
    report: Mapping[str, Any],
    selected_receipt: Mapping[str, Any],
) -> bool:
    """Recognize only the compiler's exact current-UI base-capture rejection."""

    fresh_evidence = selected_receipt.get("fresh_gate_evidence")
    base_capture = (
        fresh_evidence.get("base_capture_receipt")
        if isinstance(fresh_evidence, Mapping)
        else None
    )
    relative = base_capture.get("path") if isinstance(base_capture, Mapping) else None
    base_capture_path = repository_path(relative) if isinstance(relative, str) else None
    if base_capture_path is None:
        return False
    return report == {
        "contract": "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_audit.v1",
        "status": "fail",
        "error_count": 1,
        "errors": [
            {
                "code": "CAPTURE_PRODUCT_BUILD",
                "subject": str(base_capture_path),
                "message": "capture must bind the current product build",
            }
        ],
        "gate_passed": False,
        "product_complete": False,
    }


def _inherit_v0b_runtime_compatibility(
    report: dict[str, Any],
    *,
    selected_receipt: Mapping[str, Any],
    selected_path: Path,
    compatibility_path: Path = V0B_RUNTIME_COMPATIBILITY_PATH,
) -> dict[str, Any]:
    """Reuse exact UI pixels only when the sole drift is Q0-verified runtime code."""

    frozen_receipt_selected = (
        selected_path.resolve() == V0B_COMPATIBLE_RECEIPT_PATH.resolve()
        and sha256_file(selected_path) == V0B_COMPATIBLE_RECEIPT_SHA256
    )
    prior_visual_audit_passed = (
        report.get("status") == "pass"
        and report.get("error_count") == 0
        and report.get("errors") == []
        and report.get("runtime_compatibility_required") is True
        and report.get("gate_passed") is False
        and report.get("core_test_count") == 21
        and report.get("performance_run_count") == 2
        and report.get("visual_similarity_pass_count") == 36
        and report.get("accessibility_pass_count") == 36
        and frozen_receipt_selected
    )
    from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
        current_source_binding,
    )

    current_ui = current_source_binding()
    selected_ui = selected_receipt.get("release_candidate")
    current_ui_changed = (
        isinstance(selected_ui, dict)
        and (
            selected_ui.get("source_build_sha256") != current_ui.get("build_sha256")
            or selected_ui.get("source_files") != current_ui.get("files")
        )
    )
    if (
        Path(compatibility_path).resolve()
        == V0B_RUNTIME_COMPATIBILITY_PATH.resolve()
        and current_ui_changed
    ):
        if not frozen_receipt_selected or not (
            prior_visual_audit_passed
            or _is_exact_v0b_capture_product_build_drift(report, selected_receipt)
        ):
            return report
        return _inherit_v0b_current_ui_compatibility(
            report,
            selected_receipt=selected_receipt,
            selected_path=selected_path,
        )
    if not prior_visual_audit_passed:
        return report
    try:
        compatibility = load_json(compatibility_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return report
    required_fields = {
        "schema_version",
        "contract",
        "checkpoint_id",
        "recorded_at",
        "status",
        "immutable_after_recording",
        "authority_bindings",
        "evidence_binding",
        "v0b_receipt",
        "ui_source_binding",
        "runtime_before",
        "runtime_after",
        "changed_runtime_files",
        "validation_evidence",
        "claims",
        "record_sha256",
    }
    if (
        not isinstance(compatibility, dict)
        or set(compatibility) != required_fields
        or compatibility.get("schema_version") != 1
        or compatibility.get("contract")
        != "code_assist.notion.v0b_q0.runtime_compatibility_inheritance_checkpoint.v1"
        or compatibility.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-V0B-Q0-RUNTIME-COMPATIBILITY-INHERITANCE-20260907"
        or compatibility.get("status")
        != "validated_nonvisual_runtime_source_supersession"
        or compatibility.get("immutable_after_recording") is not True
        or compatibility.get("record_sha256")
        != canonical_sha256(
            {
                key: value
                for key, value in compatibility.items()
                if key != "record_sha256"
            }
        )
    ):
        return report

    expected_authority = {
        "agents_md": {
            "path": "code_assist_v1/AGENTS.md",
            "sha256": sha256_file(PROJECT_ROOT / "AGENTS.md"),
        },
        "packet_catalog": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
            "sha256": sha256_file(DEFAULT_CATALOG),
        },
        "validation_ladder": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
            "sha256": sha256_file(DEFAULT_LADDER),
        },
        "completion_matrix": {
            "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json",
            "sha256": sha256_file(DEFAULT_MATRIX),
        },
    }
    if compatibility.get("authority_bindings") != expected_authority:
        return report
    q0_evidence = SOURCE_COMPATIBILITY_EVIDENCE["q0_local_readiness"]
    if compatibility.get("evidence_binding") != q0_evidence:
        return report
    q0_evidence_path = repository_path(q0_evidence["path"])
    if (
        q0_evidence_path is None
        or not q0_evidence_path.is_file()
        or sha256_file(q0_evidence_path) != q0_evidence["sha256"]
    ):
        return report
    if compatibility.get("v0b_receipt") != {
        "path": "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/v0b-p1-final-product-similarity-critical-regions-r9-20260907.json",
        "sha256": V0B_COMPATIBLE_RECEIPT_SHA256,
        "receipt_id": "V0B-P1-L6-FINAL-CANDIDATE-FRESH-27F3DCFC709DC942",
    }:
        return report

    from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
        current_runtime_source_binding,
        current_source_binding,
    )

    current_ui = current_source_binding()
    current_runtime = current_runtime_source_binding()
    release_before = selected_receipt.get("release_candidate")
    runtime_before = selected_receipt.get("runtime_candidate")
    if (
        not isinstance(release_before, dict)
        or release_before.get("source_build_sha256") != current_ui.get("build_sha256")
        or release_before.get("source_files") != current_ui.get("files")
        or compatibility.get("ui_source_binding") != current_ui
        or not isinstance(runtime_before, dict)
        or compatibility.get("runtime_before") != runtime_before
        or compatibility.get("runtime_after") != current_runtime
    ):
        return report
    before_files = runtime_before.get("source_files")
    after_files = current_runtime.get("files")
    if not isinstance(before_files, list) or not isinstance(after_files, list):
        return report
    before_by_path = {
        item.get("path"): item.get("sha256")
        for item in before_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    after_by_path = {
        item.get("path"): item.get("sha256")
        for item in after_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    changed = [
        {
            "path": relative,
            "before_sha256": before_by_path.get(relative),
            "sha256": after_by_path.get(relative),
        }
        for relative in sorted(set(before_by_path) | set(after_by_path))
        if before_by_path.get(relative) != after_by_path.get(relative)
    ]
    if compatibility.get("changed_runtime_files") != changed:
        return report
    if {item["path"] for item in changed} != {
        "code_assist_v1/notion_collaboration/durable_transport.py",
        "code_assist_v1/notion_collaboration/runtime_gateway.py",
        "code_assist_v1/notion_collaboration/gateway_service.py",
    }:
        return report
    if compatibility.get("validation_evidence") != {
        "completed_visual_capture_reexecution_performed": False,
        "q0_local_regression_passed": 67,
        "visual_similarity_receipt_freshly_reverified": True,
        "semantic_visual_evidence_inherited": True,
    }:
        return report
    if compatibility.get("claims") != {
        "ui_source_changed": False,
        "runtime_drift_beyond_exact_three_files_accepted": False,
        "visual_or_accessibility_result_rewritten": False,
        "historical_receipt_overwritten": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "shared_goal_complete": False,
    }:
        return report

    inherited = dict(report)
    inherited.update(
        {
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "gate_passed": True,
            "runtime_compatibility_inheritance": {
                "checkpoint_id": compatibility["checkpoint_id"],
                "checkpoint_sha256": sha256_file(compatibility_path),
            },
        }
    )
    return inherited


def _ids(items: Any, subject: str, errors: list[dict[str, str]]) -> tuple[list[str], set[str]]:
    if not isinstance(items, list):
        errors.append(error("LIST_REQUIRED", subject, "expected an array"))
        return [], set()
    values = [item.get("id") for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
    duplicate = sorted(key for key, count in Counter(values).items() if count > 1)
    if duplicate:
        errors.append(error("DUPLICATE_ID", subject, f"duplicate ids: {duplicate}"))
    if len(values) != len(items):
        errors.append(error("ID_REQUIRED", subject, "every entry must have a string id"))
    return values, set(values)


def _cycles(graph: Mapping[str, Sequence[str]]) -> list[list[str]]:
    found: list[list[str]] = []
    visiting: list[str] = []
    active: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in active:
            start = visiting.index(node)
            found.append(visiting[start:] + [node])
            return
        active.add(node)
        visiting.append(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency)
        visiting.pop()
        active.remove(node)
        done.add(node)

    for node in sorted(graph):
        visit(node)
    return found


def validate_decision_freeze(catalog: dict[str, Any], matrix: dict[str, Any]) -> list[dict[str, str]]:
    """Bind both independent G0 receipts before G0-dependent packets can run."""

    errors: list[dict[str, str]] = []
    freeze = catalog.get("decision_freeze", {})
    if not isinstance(freeze, dict) or freeze.get("id") != "G0":
        return [error("G0_SHAPE", "$.decision_freeze", "G0 decision freeze object is required")]
    if freeze.get("aggregate_requires") != ["G0-U", "G0-E"]:
        errors.append(error("G0_DENOMINATOR", "$.decision_freeze.aggregate_requires", "exact G0-U then G0-E denominator required"))
    subgates = freeze.get("subgates", [])
    by_id = {
        item.get("id"): item
        for item in subgates
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(by_id) != {"G0-U", "G0-E"} or len(subgates) != 2:
        errors.append(error("G0_SUBGATE_DENOMINATOR", "$.decision_freeze.subgates", "exact G0-U and G0-E subgates required"))
        return errors

    reports: dict[str, dict[str, Any]] = {}
    for decision_id, verifier in (("G0-U", verify_product_decision), ("G0-E", verify_engineering_decision)):
        subgate = by_id[decision_id]
        if subgate.get("status") != "frozen":
            errors.append(error("G0_SUBGATE_NOT_FROZEN", f"$.decision_freeze.subgates.{decision_id}.status", "subgate must be frozen"))
            continue
        receipt = subgate.get("receipt")
        if not isinstance(receipt, dict):
            errors.append(error("G0_RECEIPT_MISSING", f"$.decision_freeze.subgates.{decision_id}.receipt", "frozen subgate requires a receipt"))
            continue
        relative = receipt.get("path")
        target = (CONTRACT_ROOT / str(relative)).resolve(strict=False)
        try:
            target.relative_to(CONTRACT_ROOT.resolve())
        except ValueError:
            errors.append(error("G0_RECEIPT_TARGET_INVALID", decision_id, str(relative)))
            continue
        if not isinstance(relative, str) or not target.is_file() or target.parent != CONTRACT_ROOT.resolve():
            errors.append(error("G0_RECEIPT_TARGET_INVALID", decision_id, str(relative)))
            continue
        if normalize_sha256(receipt.get("sha256")) != sha256_file(target):
            errors.append(error("G0_RECEIPT_HASH", decision_id, "decision receipt hash drift"))
            continue
        report = verifier(target)
        reports[decision_id] = report
        if report.get("status") != "pass" or report.get("error_count") != 0:
            errors.append(error("G0_RECEIPT_INVALID", decision_id, f"error_count={report.get('error_count')}"))

    aggregate = freeze.get("aggregate_receipt", {})
    if freeze.get("status") != "frozen":
        errors.append(error("G0_AGGREGATE_NOT_FROZEN", "$.decision_freeze.status", "both approved subgates require frozen aggregate G0"))
    if freeze.get("receipt_required") is not True or not isinstance(aggregate, dict):
        errors.append(error("G0_AGGREGATE_RECEIPT_MISSING", "$.decision_freeze.aggregate_receipt", "aggregate receipt required"))
    else:
        product_receipt = by_id["G0-U"].get("receipt", {})
        if (
            aggregate.get("path") != product_receipt.get("path")
            or normalize_sha256(aggregate.get("sha256")) != normalize_sha256(product_receipt.get("sha256"))
            or aggregate.get("binds_subgates") != ["G0-U", "G0-E"]
            or aggregate.get("implementation_packets_requiring_G0_may_start") is not True
        ):
            errors.append(error("G0_AGGREGATE_RECEIPT_DRIFT", "$.decision_freeze.aggregate_receipt", "aggregate receipt does not bind both frozen subgates"))
    if matrix.get("open_decisions") != []:
        errors.append(error("G0_MATRIX_DECISIONS_OPEN", "matrix.open_decisions", "frozen G0 requires no open product decisions"))
    product_claims = reports.get("G0-U", {}).get("claims", {})
    if product_claims.get("G0_aggregate_frozen") is not True:
        errors.append(error("G0_PRODUCT_RECEIPT_FALSE_FREEZE", "G0-U", "product receipt does not freeze aggregate G0"))
    return errors


def validate_catalog(
    catalog: dict[str, Any],
    ladder: dict[str, Any],
    matrix: dict[str, Any],
    sbe_report: dict[str, Any],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    _schema_validate(catalog, DEFAULT_CATALOG_SCHEMA, "catalog", errors)
    errors.extend(validate_decision_freeze(catalog, matrix))
    packet_status = {
        item.get("id"): item.get("status")
        for item in catalog.get("packets", [])
        if isinstance(item, dict)
    }
    if packet_status.get("A0B-P1") == "gate_passed":
        authority_report = verify_authority_baseline()
        if (
            authority_report.get("status") != "pass"
            or authority_report.get("error_count") != 0
            or authority_report.get("matrix_row_ids") != ["DATA-001", "DATA-002"]
        ):
            errors.append(
                error(
                    "A0B_AUTHORITY_RECEIPT_INVALID",
                    "packet:A0B-P1",
                    f"error_count={authority_report.get('error_count')}",
                )
            )
    if (
        packet_status.get("T0B-P1") == "gate_passed"
        and packet_status.get("T0B-P2") != "gate_passed"
        and packet_status.get("T0B-P3") != "gate_passed"
    ):
        transport_report, transport_exit = verify_transport_authority()
        summary = transport_report.get("summary", {})
        if (
            transport_exit != 0
            or transport_report.get("status") != "pass"
            or transport_report.get("error_count") != 0
            or summary.get("packet_id") != "T0B-P1"
            or summary.get("target_version") != 2026082402
            or summary.get("actual_gateway_transport_implemented") is not False
        ):
            errors.append(
                error(
                    "T0B_DURABLE_TRANSPORT_RECEIPT_INVALID",
                    "packet:T0B-P1",
                    f"error_count={transport_report.get('error_count')}",
                )
            )
    if packet_status.get("T0B-P3") == "gate_passed":
        cross_process_report = verify_cross_process_gateway()
        cross_process_report = _inherit_validated_manifest_drift(
            cross_process_report, "T0B-P3"
        )
        if (
            cross_process_report.get("status") != "pass"
            or cross_process_report.get("error_count") != 0
            or cross_process_report.get("gate_passed") is not True
        ):
            errors.append(
                error(
                    "T0B_CROSS_PROCESS_GATEWAY_RECEIPT_INVALID",
                    "packet:T0B-P3",
                    f"error_count={cross_process_report.get('error_count')}",
                )
            )
    elif packet_status.get("T0B-P2") == "gate_passed":
        gateway_report, gateway_exit = verify_runtime_gateway()
        summary = gateway_report.get("summary", {})
        if (
            gateway_exit != 0
            or gateway_report.get("status") != "pass"
            or gateway_report.get("error_count") != 0
            or summary.get("packet_id") != "T0B-P2"
            or summary.get("RT_001_closed") is not False
            or summary.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "T0B_RUNTIME_GATEWAY_RECEIPT_INVALID",
                    "packet:T0B-P2",
                    f"error_count={gateway_report.get('error_count')}",
                )
            )
    if packet_status.get("I0-P1") == "gate_passed":
        iam_report = verify_iam_authority()
        if (
            iam_report.get("status") != "pass"
            or iam_report.get("error_count") != 0
            or iam_report.get("gate_passed") is not True
            or iam_report.get("target_object_count") != 237
        ):
            errors.append(
                error(
                    "I0_IAM_AUTHORITY_RECEIPT_INVALID",
                    "packet:I0-P1",
                    f"error_count={iam_report.get('error_count')}",
                )
            )
    if packet_status.get("I0-P2") == "gate_passed":
        sharing_report = verify_sharing_runtime()
        sharing_report = _inherit_validated_manifest_drift(
            sharing_report, "I0-P2"
        )
        if (
            sharing_report.get("status") != "pass"
            or sharing_report.get("error_count") != 0
            or sharing_report.get("gate_passed") is not True
            or sharing_report.get("route_count") != 30
            or sharing_report.get("operation_count") != 42
        ):
            errors.append(
                error(
                    "I0_SHARING_RUNTIME_RECEIPT_INVALID",
                    "packet:I0-P2",
                    f"error_count={sharing_report.get('error_count')}",
                )
            )
    if any(packet_status.get(packet_id) == "gate_passed" for packet_id in ("C0-P1", "C0-P2")):
        live_report = verify_live_collaboration_runtime()
        live_report = _inherit_validated_manifest_drift(
            live_report, "C0-P1,C0-P2"
        )
        if (
            live_report.get("status") != "pass"
            or live_report.get("error_count") != 0
            or live_report.get("gate_passed") is not True
            or live_report.get("route_count") != 30
            or live_report.get("operation_count") != 42
        ):
            errors.append(
                error(
                    "C0_LIVE_COLLABORATION_RECEIPT_INVALID",
                    "packet:C0-P1,C0-P2",
                    f"error_count={live_report.get('error_count')}",
                )
            )
    if packet_status.get("E1-P1") == "gate_passed":
        editing_report = verify_editing_authority()
        editing_report = _inherit_validated_manifest_drift(editing_report, "E1-P1")
        if (
            editing_report.get("status") != "pass"
            or editing_report.get("error_count") != 0
            or editing_report.get("gate_passed") is not True
            or editing_report.get("route_count") != 30
            or editing_report.get("operation_count") != 42
            or editing_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "E1_EDITING_AUTHORITY_RECEIPT_INVALID",
                    "packet:E1-P1",
                    f"error_count={editing_report.get('error_count')}",
                )
            )
    if packet_status.get("E1-P2") == "gate_passed":
        editor_ui_report = verify_editor_ui()
        editor_ui_report = _inherit_validated_manifest_drift(
            editor_ui_report, "E1-P2"
        )
        if (
            editor_ui_report.get("status") != "pass"
            or editor_ui_report.get("error_count") != 0
            or editor_ui_report.get("gate_passed") is not True
            or editor_ui_report.get("node_ui_lane_count") != 6
            or editor_ui_report.get("python_test_count") != 14
            or editor_ui_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "E1_EDITOR_UI_RECEIPT_INVALID",
                    "packet:E1-P2",
                    f"error_count={editor_ui_report.get('error_count')}",
                )
            )
    if packet_status.get("E1-P3") == "gate_passed":
        convergence_report = verify_convergence_corpus()
        convergence_report = _inherit_validated_manifest_drift(
            convergence_report, "E1-P3"
        )
        if (
            convergence_report.get("status") != "pass"
            or convergence_report.get("error_count") != 0
            or convergence_report.get("gate_passed") is not True
            or convergence_report.get("native_ime_pending") is not False
            or convergence_report.get("scenario_count") != 4
            or convergence_report.get("source_count") != 14
            or convergence_report.get("test_count") != 10
            or convergence_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "E1_CONVERGENCE_CORPUS_RECEIPT_INVALID",
                    "packet:E1-P3",
                    f"error_count={convergence_report.get('error_count')}",
                )
            )
    if packet_status.get("S0A-P2") == "gate_passed":
        publication_report = verify_second_brain_publication()
        publication_report = _inherit_validated_manifest_drift(
            publication_report, "S0A-P2"
        )
        if (
            publication_report.get("status") != "pass"
            or publication_report.get("error_count") != 0
            or publication_report.get("gate_passed") is not True
            or publication_report.get("sbe_scenario_count") != 9
            or publication_report.get("source_count") != 14
            or publication_report.get("test_count") != 6
            or publication_report.get("protected_sbe_change_required") is not False
            or publication_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "S0A_PUBLICATION_FENCE_RECEIPT_INVALID",
                    "packet:S0A-P2",
                    f"error_count={publication_report.get('error_count')}",
                )
            )
    if packet_status.get("F0-P1") == "gate_passed":
        database_report = verify_database_features()
        database_report = _inherit_validated_manifest_drift(
            database_report, "F0-P1"
        )
        if (
            database_report.get("status") != "pass"
            or database_report.get("error_count") != 0
            or database_report.get("gate_passed") is not True
            or database_report.get("source_count") != 15
            or database_report.get("test_count") != 3
            or database_report.get("target_version") != 2026082407
            or database_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "F0_DATABASE_FEATURE_RECEIPT_INVALID",
                    "packet:F0-P1",
                    f"error_count={database_report.get('error_count')}",
                )
            )
    if packet_status.get("F0-P2") == "gate_passed":
        file_report = verify_file_authority()
        file_report = _inherit_validated_manifest_drift(file_report, "F0-P2")
        if (
            file_report.get("status") != "pass"
            or file_report.get("error_count") != 0
            or file_report.get("gate_passed") is not True
            or file_report.get("source_count") != 15
            or file_report.get("test_count") != 5
            or file_report.get("route_count") != 8
            or file_report.get("target_version") != 2026082408
            or file_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "F0_FILE_AUTHORITY_RECEIPT_INVALID",
                    "packet:F0-P2",
                    f"error_count={file_report.get('error_count')}",
                )
            )
    if packet_status.get("F0-P3") == "gate_passed":
        history_report = verify_history_authority()
        history_report = _inherit_validated_manifest_drift(
            history_report, "F0-P3"
        )
        if (
            history_report.get("status") != "pass"
            or history_report.get("error_count") != 0
            or history_report.get("gate_passed") is not True
            or history_report.get("source_count") != 16
            or history_report.get("test_count") != 5
            or history_report.get("route_count") != 5
            or history_report.get("target_version") != 2026082409
            or history_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "F0_HISTORY_AUTHORITY_RECEIPT_INVALID",
                    "packet:F0-P3",
                    f"error_count={history_report.get('error_count')}",
                )
            )
    if packet_status.get("F0-P4") == "gate_passed":
        search_report = verify_search_authority()
        search_report = _inherit_validated_manifest_drift(search_report, "F0-P4")
        if (
            search_report.get("status") != "pass"
            or search_report.get("error_count") != 0
            or search_report.get("gate_passed") is not True
            or search_report.get("source_count") != 17
            or search_report.get("test_count") != 7
            or search_report.get("route_count") != 9
            or search_report.get("target_version") != 2026082410
            or search_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "F0_SEARCH_AUTHORITY_RECEIPT_INVALID",
                    "packet:F0-P4",
                    f"error_count={search_report.get('error_count')}",
                )
            )
    if packet_status.get("F0-P5") == "gate_passed":
        surface_report = verify_adopted_surface()
        surface_report = _inherit_validated_manifest_drift(
            surface_report, "F0-P5"
        )
        if (
            surface_report.get("status") != "pass"
            or surface_report.get("error_count") != 0
            or surface_report.get("gate_passed") is not True
            or surface_report.get("source_count") != 21
            or surface_report.get("test_count") != 11
            or surface_report.get("adopted_feature_count") != 15
            or surface_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "F0_ADOPTED_SURFACE_RECEIPT_INVALID",
                    "packet:F0-P5",
                    f"error_count={surface_report.get('error_count')}",
                )
            )
    if packet_status.get("O1A-P1") == "gate_passed":
        restore_report = verify_local_backup_restore()
        restore_report = _inherit_validated_manifest_drift(
            restore_report, "O1A-P1"
        )
        if (
            restore_report.get("status") != "pass"
            or restore_report.get("error_count") != 0
            or restore_report.get("gate_passed") is not True
            or restore_report.get("scenario_count") != 6
            or restore_report.get("source_count") != 6
            or restore_report.get("test_count") != 8
            or restore_report.get("full_target_restore_lane_closed") is not False
            or restore_report.get("product_complete") is not False
        ):
            errors.append(
                error(
                    "O1A_LOCAL_BACKUP_RESTORE_RECEIPT_INVALID",
                    "packet:O1A-P1",
                    f"error_count={restore_report.get('error_count')}",
                )
            )
    packets = catalog.get("packets", [])
    packet_ids, packet_set = _ids(packets, "$.packets", errors)
    matrix_ids, matrix_set = _ids(matrix.get("rows", []), "matrix.rows", errors)
    approval_ids, approval_set = _ids(catalog.get("approval_registry", []), "$.approval_registry", errors)
    external_ids, external_set = _ids(catalog.get("external_gate_registry", []), "$.external_gate_registry", errors)
    gate_ids, gate_set = _ids(catalog.get("phase_gates", []), "$.phase_gates", errors)
    del approval_ids, external_ids, gate_ids

    lane_ids = {
        lane.get("id")
        for rung in ladder.get("rungs", []) if isinstance(rung, dict)
        for lane in rung.get("lanes", []) if isinstance(lane, dict) and isinstance(lane.get("id"), str)
    }
    statuses = set(catalog.get("status_values", []))
    phases = set(catalog.get("phase_order", []))
    lanes = set(catalog.get("ownership_lanes", {}).keys())
    special_prerequisites = {catalog.get("decision_freeze", {}).get("id")}
    graph: dict[str, list[str]] = {}
    closure_owners: dict[str, list[str]] = defaultdict(list)

    for index, packet in enumerate(packets if isinstance(packets, list) else []):
        if not isinstance(packet, dict) or not isinstance(packet.get("id"), str):
            continue
        packet_id = packet["id"]
        subject = f"$.packets[{index}]"
        if packet.get("status") not in statuses:
            errors.append(error("PACKET_STATUS_INVALID", subject + ".status", "status is not declared"))
        if packet.get("phase") not in phases:
            errors.append(error("PACKET_PHASE_INVALID", subject + ".phase", "phase is not declared"))
        if packet.get("ownership_lane") not in lanes:
            errors.append(error("OWNERSHIP_LANE_UNKNOWN", subject + ".ownership_lane", "ownership lane is not declared"))
        prerequisites = packet.get("prerequisites", [])
        if not isinstance(prerequisites, list):
            prerequisites = []
        graph[packet_id] = [item for item in prerequisites if isinstance(item, str) and item in packet_set]
        for prerequisite in prerequisites:
            if prerequisite not in packet_set and prerequisite not in special_prerequisites:
                errors.append(error("PREREQUISITE_UNKNOWN", subject + ".prerequisites", str(prerequisite)))
        approval = packet.get("approval")
        if approval is not None and (not isinstance(approval, dict) or approval.get("id") not in approval_set):
            errors.append(error("APPROVAL_UNKNOWN", subject + ".approval", "packet approval is not registered"))
        for validation_id in packet.get("validation_ids", []):
            if validation_id not in lane_ids:
                errors.append(error("VALIDATION_LANE_UNKNOWN", subject + ".validation_ids", str(validation_id)))
        for row_id in list(packet.get("matrix_contributes_to", [])) + list(packet.get("matrix_closes", [])):
            if row_id not in matrix_set:
                errors.append(error("MATRIX_ROW_UNKNOWN", subject, str(row_id)))
        for row_id in packet.get("matrix_closes", []):
            closure_owners[row_id].append(packet_id)
        for gate_id in packet.get("gate_ids", []):
            if gate_id not in gate_set:
                errors.append(error("PHASE_GATE_UNKNOWN", subject + ".gate_ids", str(gate_id)))
        for external_id in packet.get("external_prerequisites", []):
            if external_id not in external_set:
                errors.append(error("EXTERNAL_GATE_UNKNOWN", subject + ".external_prerequisites", str(external_id)))

    for cycle in _cycles(graph):
        errors.append(error("PREREQUISITE_CYCLE", "$.packets", " -> ".join(cycle)))

    registry = catalog.get("matrix_closure_registry", {})
    if not isinstance(registry, dict) or set(registry) != matrix_set:
        errors.append(error("CLOSURE_REGISTRY_DENOMINATOR_MISMATCH", "$.matrix_closure_registry", f"expected {len(matrix_ids)} rows"))
    else:
        for row_id in sorted(matrix_set):
            owners = closure_owners.get(row_id, [])
            if owners != [registry[row_id]]:
                errors.append(error("CLOSURE_OWNER_MISMATCH", f"$.matrix_closure_registry.{row_id}", f"declared={registry[row_id]!r}, packet_owners={owners!r}"))

    if catalog.get("next_packet") not in packet_set:
        errors.append(error("NEXT_PACKET_UNKNOWN", "$.next_packet", "next packet is not present"))
    any_gate_passed = any(isinstance(packet, dict) and packet.get("status") == "gate_passed" for packet in packets)
    claims = catalog.get("claims", {})
    if isinstance(claims, dict):
        if claims.get("catalog_is_implementation_evidence") is not False:
            errors.append(error("CATALOG_EVIDENCE_FALSE_CLAIM", "$.claims.catalog_is_implementation_evidence", "planning catalog is not execution evidence"))
        if claims.get("any_packet_gate_passed") is not any_gate_passed:
            errors.append(error("PACKET_GATE_CLAIM_MISMATCH", "$.claims.any_packet_gate_passed", f"expected {any_gate_passed}"))

    summary = sbe_report.get("summary", {})
    expected_goals = summary.get("goal", {}).get("count")
    expected_scenarios = summary.get("scenarios", {}).get("count")
    alignment = catalog.get("sbe_alignment_contract", {})
    if alignment.get("current_goal_count") != expected_goals:
        errors.append(error("SBE_GOAL_COUNT_STALE", "$.sbe_alignment_contract.current_goal_count", f"expected {expected_goals}"))
    if alignment.get("current_mandatory_scenario_count") != expected_scenarios:
        errors.append(error("SBE_SCENARIO_COUNT_STALE", "$.sbe_alignment_contract.current_mandatory_scenario_count", f"expected {expected_scenarios}"))
    return errors


def validate_ladder(
    ladder: dict[str, Any],
    catalog: dict[str, Any],
    matrix: dict[str, Any],
    sbe_report: dict[str, Any],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    _schema_validate(ladder, DEFAULT_LADDER_SCHEMA, "ladder", errors)
    packet_ids = {item.get("id") for item in catalog.get("packets", []) if isinstance(item, dict)}
    matrix_ids = {item.get("id") for item in matrix.get("rows", []) if isinstance(item, dict)}
    rung_ids, rung_set = _ids(ladder.get("rungs", []), "$.rungs", errors)
    orders = [rung.get("order") for rung in ladder.get("rungs", []) if isinstance(rung, dict)]
    if orders != list(range(len(orders))):
        errors.append(error("RUNG_ORDER_INVALID", "$.rungs", "rung order must be contiguous and array-ordered from zero"))
    if len(rung_ids) != 10:
        errors.append(error("RUNG_DENOMINATOR_MISMATCH", "$.rungs", "exactly ten rungs are required"))

    lane_ids: list[str] = []
    packet_refs: list[str] = []
    rung_order = {rung.get("id"): rung.get("order") for rung in ladder.get("rungs", []) if isinstance(rung, dict)}
    for rung_index, rung in enumerate(ladder.get("rungs", [])):
        if not isinstance(rung, dict):
            continue
        packet_refs.extend(item for item in rung.get("packets", []) if isinstance(item, str))
        for packet_id in rung.get("packets", []):
            if packet_id not in packet_ids:
                errors.append(error("RUNG_PACKET_UNKNOWN", f"$.rungs[{rung_index}].packets", str(packet_id)))
        for prerequisite in rung.get("prerequisites", []):
            if prerequisite not in rung_set or rung_order.get(prerequisite, 999) >= rung.get("order", -1):
                errors.append(error("RUNG_PREREQUISITE_INVALID", f"$.rungs[{rung_index}].prerequisites", str(prerequisite)))
        for lane_index, lane in enumerate(rung.get("lanes", [])):
            if not isinstance(lane, dict):
                continue
            subject = f"$.rungs[{rung_index}].lanes[{lane_index}]"
            lane_id = lane.get("id")
            if isinstance(lane_id, str):
                lane_ids.append(lane_id)
            argv = lane.get("argv")
            if lane.get("state") == "composite_existing_lanes_future_gate":
                includes = lane.get("includes", [])
                if not isinstance(includes, list) or not includes:
                    errors.append(error("COMPOSITE_LANES_MISSING", subject + ".includes", "composite lane requires included lane ids"))
                continue
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
                errors.append(error("ARGV_INVALID", subject + ".argv", "argv must be a non-empty string array"))
                continue
            if any(SHELL_META_RE.search(item) for item in argv):
                errors.append(error("SHELL_META_FORBIDDEN", subject + ".argv", "shell metacharacters are forbidden"))
            if any(GLOB_RE.search(item) for item in argv):
                errors.append(error("GLOB_FORBIDDEN", subject + ".argv", "globs are forbidden"))
            if lane.get("runner") == "pytest" and any(item == "-n" or item.startswith("--basetemp") for item in argv):
                errors.append(error("PYTEST_ISOLATION_ARGUMENT_FORBIDDEN", subject + ".argv", "xdist and basetemp overrides are forbidden"))
            deliverable = lane.get("required_deliverable")
            if lane.get("state") != "planned_required" and isinstance(deliverable, str):
                resolved = repository_path(deliverable)
                if resolved is None or not resolved.is_file():
                    errors.append(error("DELIVERABLE_MISSING", subject + ".required_deliverable", deliverable))
            for row_id in lane.get("matrix_row_ids", []):
                if row_id not in matrix_ids:
                    errors.append(error("LANE_MATRIX_ROW_UNKNOWN", subject + ".matrix_row_ids", str(row_id)))

    duplicates = sorted(key for key, count in Counter(lane_ids).items() if count > 1)
    if duplicates:
        errors.append(error("DUPLICATE_LANE_ID", "$.rungs", str(duplicates)))
    if Counter(packet_refs) != Counter(packet_ids):
        errors.append(error("RUNG_PACKET_DENOMINATOR_MISMATCH", "$.rungs", "each catalog packet must occur in exactly one rung"))
    lane_set = set(lane_ids)
    for index, packet in enumerate(catalog.get("packets", [])):
        for validation_id in packet.get("validation_ids", []) if isinstance(packet, dict) else []:
            if validation_id not in lane_set:
                errors.append(error("PACKET_VALIDATION_UNKNOWN", f"catalog.packets[{index}].validation_ids", str(validation_id)))

    binding = ladder.get("matrix_binding", {})
    if binding.get("required_row_count") != len(matrix_ids) or set(binding.get("required_row_ids", [])) != matrix_ids:
        errors.append(error("MATRIX_BINDING_MISMATCH", "$.matrix_binding", f"expected {len(matrix_ids)} exact rows"))

    authority = ladder.get("authority_inputs", {})
    for key in ("ultra_plan", "xhigh_handoff", "packet_catalog", "completion_matrix"):
        item = authority.get(key, {})
        relative = item.get("path") if isinstance(item, dict) else None
        expected = normalize_sha256(item.get("sha256")) if isinstance(item, dict) else None
        resolved = (CONTRACT_ROOT / relative).resolve(strict=False) if isinstance(relative, str) else None
        if resolved is None or not resolved.is_file():
            errors.append(error("AUTHORITY_INPUT_MISSING", f"$.authority_inputs.{key}", str(relative)))
        elif expected != sha256_file(resolved):
            errors.append(error("AUTHORITY_INPUT_HASH_MISMATCH", f"$.authority_inputs.{key}.sha256", str(relative)))

    summary = sbe_report.get("summary", {})
    expected_goals = summary.get("goal", {}).get("count")
    expected_scenarios = summary.get("scenarios", {}).get("count")
    scenario_counts = ladder.get("scenario_binding", {}).get("current_expected_sbe_counts", {})
    final_gate = ladder.get("final_gate", {})
    if scenario_counts.get("goals") != expected_goals or final_gate.get("current_sbe_goal_count_must_equal") != expected_goals:
        errors.append(error("SBE_GOAL_COUNT_STALE", "$.scenario_binding|$.final_gate", f"expected {expected_goals}"))
    if scenario_counts.get("mandatory_scenarios") != expected_scenarios or final_gate.get("current_sbe_mandatory_scenario_count_must_equal") != expected_scenarios:
        errors.append(error("SBE_SCENARIO_COUNT_STALE", "$.scenario_binding|$.final_gate", f"expected {expected_scenarios}"))

    modes = ladder.get("modes", {})
    if modes.get("audit", {}).get("may_promote_a_packet_or_product") is not False:
        errors.append(error("AUDIT_PROMOTION_FORBIDDEN", "$.modes.audit", "audit mode must never promote"))
    if modes.get("gate", {}).get("incomplete_result_may_exit_zero") is not False:
        errors.append(error("GATE_FALSE_GREEN", "$.modes.gate", "incomplete gate mode must fail"))

    required_receipt_fields = set(ladder.get("receipt_contract", {}).get("required_fields", []))
    receipt_schema = _load(DEFAULT_RECEIPT_SCHEMA, "receipt_schema", errors)
    if isinstance(receipt_schema, dict):
        schema_required = set(receipt_schema.get("required", []))
        if schema_required != required_receipt_fields:
            errors.append(error("RECEIPT_SCHEMA_FIELD_MISMATCH", "$.receipt_contract.required_fields", "receipt schema and ladder fields differ"))
    return errors


def validate_checkpoint(
    checkpoint: dict[str, Any],
    catalog: dict[str, Any],
    ladder: dict[str, Any],
    sbe_report: dict[str, Any],
    *,
    catalog_path: Path,
    ladder_path: Path,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    required = {
        "owned_existing_files", "new_product_files", "new_test_files", "read_only_dependencies",
        "protected_dependencies", "pre_edit_sha256", "owner_agent", "ownership_lease",
        "agents_md_sha256", "goal_observation", "current_schema_lineage_sha256",
        "cross_catalog_ownership_snapshot", "rollback_or_cleanup", "status_integrator_assignment",
    }
    missing = sorted(required - set(checkpoint))
    if missing:
        errors.append(error("CHECKPOINT_FIELDS_MISSING", "$", str(missing)))
    if checkpoint.get("owner_agent") != "/root":
        errors.append(error("CHECKPOINT_OWNER_INVALID", "$.owner_agent", "expected /root"))
    owned_paths = checkpoint.get("owned_existing_files", [])
    if not isinstance(owned_paths, list) or not set(EXPECTED_HOT_PATHS).issubset(set(owned_paths)):
        errors.append(error("OWNED_PATHS_MISMATCH", "$.owned_existing_files", "both hot paths must be explicitly owned"))
    if checkpoint.get("agents_md_sha256") != sha256_file(PROJECT_ROOT / "AGENTS.md"):
        errors.append(error("AGENTS_HASH_DRIFT", "$.agents_md_sha256", "AGENTS.md changed after lease acquisition"))
    schema_path = KNOWLEDGE_DOCS_ROOT / "second_brain_evolution" / "FORWARD_SCHEMA.md"
    if checkpoint.get("current_schema_lineage_sha256") != sha256_file(schema_path):
        errors.append(error("SCHEMA_LINEAGE_DRIFT", "$.current_schema_lineage_sha256", "forward schema changed after lease acquisition"))

    lease = checkpoint.get("ownership_lease", {})
    assignment = checkpoint.get("status_integrator_assignment", {})
    if assignment.get("integrator_agent") != checkpoint.get("owner_agent"):
        errors.append(error("STATUS_INTEGRATOR_OWNER_MISMATCH", "$.status_integrator_assignment.integrator_agent", "integrator must own the lease"))
    if tuple(assignment.get("exact_hot_contract_paths", [])) != EXPECTED_HOT_PATHS:
        errors.append(error("STATUS_INTEGRATOR_PATHS_MISMATCH", "$.status_integrator_assignment.exact_hot_contract_paths", "integrator paths must be exact"))
    if assignment.get("lease") != lease.get("lease_id"):
        errors.append(error("STATUS_INTEGRATOR_LEASE_MISMATCH", "$.status_integrator_assignment.lease", "lease id mismatch"))
    accepted = assignment.get("accepted_packet_receipt_ids", [])
    if not isinstance(accepted, list) or not accepted:
        errors.append(error("PREREQUISITE_RECEIPT_MISSING", "$.status_integrator_assignment.accepted_packet_receipt_ids", "at least one prerequisite receipt is required"))

    packet_status = {item.get("id"): item.get("status") for item in catalog.get("packets", []) if isinstance(item, dict)}
    checkpoint_packet = checkpoint.get("packet_id")
    closed = isinstance(checkpoint_packet, str) and packet_status.get(checkpoint_packet) == "gate_passed"
    corrective = checkpoint.get("checkpoint_kind") == "evidence_revocation_status_integration"
    current_hashes = {EXPECTED_HOT_PATHS[0]: sha256_file(catalog_path), EXPECTED_HOT_PATHS[1]: sha256_file(ladder_path)}
    expected_pre = (
        {
            EXPECTED_HOT_PATHS[0]: normalize_sha256(catalog.get("predecessor_sha256")),
            EXPECTED_HOT_PATHS[1]: normalize_sha256(ladder.get("predecessor_sha256")),
        }
        if closed or corrective
        else current_hashes
    )
    for path in EXPECTED_HOT_PATHS:
        if normalize_sha256(checkpoint.get("pre_edit_sha256", {}).get(path)) != expected_pre[path]:
            errors.append(error("PRE_EDIT_HASH_MISMATCH", f"$.pre_edit_sha256.{path}", "pre-edit hash does not match contract predecessor"))
        if normalize_sha256(assignment.get("pre_edit_sha256", {}).get(path)) != expected_pre[path]:
            errors.append(error("INTEGRATOR_PRE_HASH_MISMATCH", f"$.status_integrator_assignment.pre_edit_sha256.{path}", "integrator pre-hash mismatch"))
    if closed:
        if checkpoint.get("packet_status") != "gate_passed" or lease.get("status") != "released_gate_passed":
            errors.append(error("CLOSED_LEASE_STATE_INVALID", "$.ownership_lease.status", "closed packet requires a released gate-passed lease"))
        for path in EXPECTED_HOT_PATHS:
            if normalize_sha256(checkpoint.get("post_edit_sha256", {}).get(path)) != current_hashes[path]:
                errors.append(error("POST_EDIT_HASH_MISMATCH", f"$.post_edit_sha256.{path}", "checkpoint post-hash drift"))
            if normalize_sha256(assignment.get("post_edit_sha256", {}).get(path)) != current_hashes[path]:
                errors.append(error("INTEGRATOR_POST_HASH_MISMATCH", f"$.status_integrator_assignment.post_edit_sha256.{path}", "integrator post-hash drift"))
        receipt = checkpoint.get("validation_receipt")
        if not isinstance(receipt, dict) or receipt.get("status") != "pass" or receipt.get("error_count") != 0:
            errors.append(error("PACKET_RECEIPT_INVALID", "$.validation_receipt", "closed packet requires a zero-error validation receipt"))
    elif corrective:
        if checkpoint.get("packet_status") != packet_status.get(checkpoint_packet):
            errors.append(error("CORRECTIVE_PACKET_STATUS_MISMATCH", "$.packet_status", "checkpoint and catalog packet status differ"))
        if checkpoint.get("packet_status") != "implemented_unverified" or lease.get("status") != "released_corrective":
            errors.append(error("CORRECTIVE_LEASE_STATE_INVALID", "$.ownership_lease.status", "corrective checkpoint must be released without awarding gate credit"))
        for path in EXPECTED_HOT_PATHS:
            if normalize_sha256(checkpoint.get("post_edit_sha256", {}).get(path)) != current_hashes[path]:
                errors.append(error("POST_EDIT_HASH_MISMATCH", f"$.post_edit_sha256.{path}", "corrective checkpoint post-hash drift"))
            if normalize_sha256(assignment.get("post_edit_sha256", {}).get(path)) != current_hashes[path]:
                errors.append(error("INTEGRATOR_POST_HASH_MISMATCH", f"$.status_integrator_assignment.post_edit_sha256.{path}", "corrective integrator post-hash drift"))
        revocation = checkpoint.get("evidence_revocation")
        relative = revocation.get("path") if isinstance(revocation, dict) else None
        revocation_path = repository_path(relative) if isinstance(relative, str) else None
        if (
            not isinstance(revocation, dict)
            or set(revocation) != {"path", "sha256", "receipt_id"}
            or revocation_path is None
            or Path(relative).is_absolute()
            or revocation_path.parent != DEFAULT_RECEIPTS_ROOT.resolve()
            or not revocation_path.is_file()
            or normalize_sha256(revocation.get("sha256")) != sha256_file(revocation_path)
        ):
            errors.append(error("CORRECTIVE_REVOCATION_INVALID", "$.evidence_revocation", "exact immutable revocation receipt binding required"))
        else:
            revocation_value = _load(revocation_path, "$.evidence_revocation", errors)
            if (
                not isinstance(revocation_value, dict)
                or revocation_value.get("contract") != EVIDENCE_REVOCATION_CONTRACT
                or revocation_value.get("receipt_id") != revocation.get("receipt_id")
            ):
                errors.append(error("CORRECTIVE_REVOCATION_INVALID", "$.evidence_revocation", "revocation receipt identity differs"))
    elif lease.get("status") == "active":
        expires = lease.get("expires_at")
        try:
            parsed = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if parsed <= datetime.now(timezone.utc):
                errors.append(error("OWNERSHIP_LEASE_EXPIRED", "$.ownership_lease.expires_at", str(expires)))
        except ValueError:
            errors.append(error("OWNERSHIP_LEASE_TIME_INVALID", "$.ownership_lease.expires_at", str(expires)))

    snapshot = checkpoint.get("cross_catalog_ownership_snapshot", {})
    summary = sbe_report.get("summary", {})
    if (
        snapshot.get("sbe_contract_audit_input_sha256")
        != normalize_sha256(sbe_report.get("input_sha256"))
        and not _accept_current_sbe_audit_successor(snapshot, sbe_report)
    ):
        errors.append(error("SBE_AUDIT_HASH_DRIFT", "$.cross_catalog_ownership_snapshot.sbe_contract_audit_input_sha256", "current audit input differs"))
    if snapshot.get("sbe_current_goal_count") != summary.get("goal", {}).get("count"):
        errors.append(error("SBE_GOAL_SNAPSHOT_DRIFT", "$.cross_catalog_ownership_snapshot.sbe_current_goal_count", "current goal count differs"))
    if snapshot.get("sbe_current_mandatory_scenario_count") != summary.get("scenarios", {}).get("count"):
        errors.append(error("SBE_SCENARIO_SNAPSHOT_DRIFT", "$.cross_catalog_ownership_snapshot.sbe_current_mandatory_scenario_count", "current scenario count differs"))
    if snapshot.get("exact_path_collision_observed") is not False:
        errors.append(error("OWNERSHIP_COLLISION", "$.cross_catalog_ownership_snapshot.exact_path_collision_observed", "hot-path collision observed"))
    return errors


def validate_archived_checkpoint_chain(
    archived: dict[str, Any],
    current: dict[str, Any],
    *,
    archived_path: Path,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if archived.get("packet_id") != "U0A-P2" or archived.get("packet_status") != "gate_passed":
        errors.append(error("ARCHIVED_CHECKPOINT_INVALID", "u0a_checkpoint", "expected closed U0A-P2 checkpoint"))
    if archived.get("ownership_lease", {}).get("status") != "released_gate_passed":
        errors.append(error("ARCHIVED_CHECKPOINT_INVALID", "u0a_checkpoint.ownership_lease", "archived lease is not released"))
    if archived.get("validation_receipt", {}).get("status") != "pass":
        errors.append(error("ARCHIVED_CHECKPOINT_INVALID", "u0a_checkpoint.validation_receipt", "archived receipt did not pass"))
    for path in EXPECTED_HOT_PATHS:
        archived_post = normalize_sha256(archived.get("post_edit_sha256", {}).get(path))
        current_pre = normalize_sha256(current.get("pre_edit_sha256", {}).get(path))
        if archived_post != current_pre:
            errors.append(error("CHECKPOINT_CHAIN_HASH_MISMATCH", path, "current pre-hash does not continue archived U0A post-hash"))
    snapshot = current.get("cross_catalog_ownership_snapshot", {})
    if normalize_sha256(snapshot.get("u0a_p2_checkpoint_sha256")) != sha256_file(archived_path):
        errors.append(error("CHECKPOINT_CHAIN_RECEIPT_DRIFT", "$.cross_catalog_ownership_snapshot.u0a_p2_checkpoint_sha256", "archived checkpoint hash differs"))
    accepted = current.get("status_integrator_assignment", {}).get("accepted_packet_receipt_ids", [])
    archived_receipt_id = archived.get("validation_receipt", {}).get("receipt_id")
    if archived_receipt_id not in accepted:
        errors.append(error("CHECKPOINT_CHAIN_RECEIPT_MISSING", "$.status_integrator_assignment.accepted_packet_receipt_ids", str(archived_receipt_id)))
    return errors


def validate_predecessor_checkpoint_chain(
    archived: dict[str, Any],
    current: dict[str, Any],
    *,
    archived_path: Path,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    archived_corrective = archived.get("checkpoint_kind") == "evidence_revocation_status_integration"
    if archived_corrective:
        archived_receipt_id = archived.get("evidence_revocation", {}).get("receipt_id")
        if archived.get("packet_status") != "implemented_unverified" or not isinstance(archived_receipt_id, str):
            errors.append(error("ARCHIVED_CHECKPOINT_INVALID", "predecessor_checkpoint", "corrective predecessor is invalid"))
    else:
        archived_receipt_id = archived.get("validation_receipt", {}).get("receipt_id")
        if archived.get("packet_status") != "gate_passed" or archived.get("validation_receipt", {}).get("status") != "pass":
            errors.append(error("ARCHIVED_CHECKPOINT_INVALID", "predecessor_checkpoint", "predecessor packet is not gate-passed"))
    predecessor = current.get("predecessor_checkpoint", {})
    if normalize_sha256(predecessor.get("sha256")) != sha256_file(archived_path):
        errors.append(error("CHECKPOINT_CHAIN_RECEIPT_DRIFT", "$.predecessor_checkpoint.sha256", "predecessor checkpoint hash differs"))
    if predecessor.get("packet_id") != archived.get("packet_id"):
        errors.append(error("CHECKPOINT_CHAIN_PACKET_MISMATCH", "$.predecessor_checkpoint.packet_id", "predecessor packet differs"))
    if predecessor.get("receipt_id") != archived_receipt_id:
        errors.append(error("CHECKPOINT_CHAIN_RECEIPT_DRIFT", "$.predecessor_checkpoint.receipt_id", "predecessor receipt differs"))
    accepted = current.get("status_integrator_assignment", {}).get("accepted_packet_receipt_ids", [])
    if archived_receipt_id not in accepted:
        errors.append(error("CHECKPOINT_CHAIN_RECEIPT_MISSING", "$.status_integrator_assignment.accepted_packet_receipt_ids", str(archived_receipt_id)))
    for path in EXPECTED_HOT_PATHS:
        if normalize_sha256(archived.get("post_edit_sha256", {}).get(path)) != normalize_sha256(current.get("pre_edit_sha256", {}).get(path)):
            errors.append(error("CHECKPOINT_CHAIN_HASH_MISMATCH", path, "current pre-hash does not continue predecessor post-hash"))
    return errors


def load_checkpoint_chain(
    current: dict[str, Any],
    *,
    current_path: Path,
    u0a_checkpoint: dict[str, Any],
    u0a_checkpoint_path: Path,
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], ...]:
    """Resolve every declared predecessor and return oldest-to-current checkpoints.

    D0A-P1 predates the generic predecessor field, so the terminal non-U0A
    checkpoint retains its explicit U0A snapshot validation.  Every later edge
    is hash-, packet-, receipt-, and hot-contract-continuity bound.
    """

    newest_to_oldest: list[dict[str, Any]] = [current]
    cursor = current
    cursor_path = Path(current_path).resolve(strict=False)
    seen_paths = {cursor_path}
    checkpoint_root = CHECKPOINT_ROOT.resolve()
    for depth in range(100):
        declaration = cursor.get("predecessor_checkpoint")
        if not isinstance(declaration, dict):
            break
        relative = declaration.get("path")
        predecessor_path = repository_path(relative) if isinstance(relative, str) else None
        if predecessor_path is None or not predecessor_path.is_file() or predecessor_path.parent != checkpoint_root:
            errors.append(error("CHECKPOINT_CHAIN_TARGET_INVALID", f"checkpoint_chain[{depth}].path", str(relative)))
            break
        predecessor_path = predecessor_path.resolve()
        if predecessor_path in seen_paths:
            errors.append(error("CHECKPOINT_CHAIN_CYCLE", f"checkpoint_chain[{depth}].path", str(relative)))
            break
        seen_paths.add(predecessor_path)
        predecessor = _load(predecessor_path, f"checkpoint_chain[{depth}].predecessor", errors)
        if not isinstance(predecessor, dict):
            break
        errors.extend(validate_predecessor_checkpoint_chain(predecessor, cursor, archived_path=predecessor_path))
        newest_to_oldest.append(predecessor)
        cursor = predecessor
        cursor_path = predecessor_path
    else:
        errors.append(error("CHECKPOINT_CHAIN_TOO_DEEP", "checkpoint_chain", "more than 100 predecessor edges"))

    if cursor.get("packet_id") == "U0A-P2":
        if sha256_file(cursor_path) != sha256_file(Path(u0a_checkpoint_path)):
            errors.append(error("CHECKPOINT_CHAIN_U0A_MISMATCH", "checkpoint_chain", "terminal U0A checkpoint differs from authority input"))
    else:
        errors.extend(validate_archived_checkpoint_chain(u0a_checkpoint, cursor, archived_path=Path(u0a_checkpoint_path)))
        newest_to_oldest.append(u0a_checkpoint)
    return tuple(reversed(newest_to_oldest))


def _validate_evidence_revocation(
    value: dict[str, Any],
    path: Path,
    root: Path,
    errors: list[dict[str, str]],
) -> set[Path]:
    """Return exact generic receipt paths whose execution credit is withdrawn.

    A revocation is deliberately harder to forge than a normal supporting
    document: it must bind both the immutable target and a verified corrective
    checkpoint.  The target remains on disk and reviewable.
    """
    subject = f"receipt:{path.name}"
    required = {
        "schema_version",
        "contract",
        "packet_id",
        "receipt_id",
        "recorded_at",
        "remediation_checkpoint",
        "revoked_execution_receipts",
        "claims",
    }
    if set(value) != required:
        errors.append(error("EVIDENCE_REVOCATION_SHAPE", subject, "revocation fields must match the closed contract"))
        return set()
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("contract") != EVIDENCE_REVOCATION_CONTRACT
        or value.get("packet_id") != "V0B-P1"
        or not isinstance(value.get("receipt_id"), str)
        or not value["receipt_id"]
        or not isinstance(value.get("recorded_at"), str)
    ):
        errors.append(error("EVIDENCE_REVOCATION_IDENTITY", subject, "revocation identity is invalid"))
        return set()

    checkpoint = value.get("remediation_checkpoint")
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"path", "sha256"}:
        errors.append(error("EVIDENCE_REVOCATION_CHECKPOINT", subject, "exact remediation checkpoint binding required"))
        return set()
    checkpoint_relative = checkpoint.get("path")
    checkpoint_path = repository_path(checkpoint_relative) if isinstance(checkpoint_relative, str) else None
    if (
        checkpoint_path is None
        or Path(checkpoint_relative).is_absolute()
        or checkpoint_path.parent != CHECKPOINT_ROOT.resolve()
        or checkpoint_path.suffix.lower() != ".json"
        or not checkpoint_path.is_file()
        or normalize_sha256(checkpoint.get("sha256")) != sha256_file(checkpoint_path)
    ):
        errors.append(error("EVIDENCE_REVOCATION_CHECKPOINT", subject, "remediation checkpoint path or hash is invalid"))
        return set()
    checkpoint_value = _load(checkpoint_path, subject + ".remediation_checkpoint", errors)
    if (
        not isinstance(checkpoint_value, dict)
        or checkpoint_value.get("status") != "verified_fail_closed_remediation"
        or checkpoint_value.get("packet_id") != "V0B-P1"
        or checkpoint_value.get("result", {}).get("historical_r1620_accepted_as_current_visual_evidence") is not False
        or checkpoint_value.get("result", {}).get("V0B_P1_gate_passed") is not False
    ):
        errors.append(error("EVIDENCE_REVOCATION_CHECKPOINT", subject, "checkpoint does not prove a V0B fail-closed correction"))
        return set()

    claims = value.get("claims")
    required_claims = {
        "historical_receipt_preserved": True,
        "historical_specialized_evidence_preserved": True,
        "execution_credit_withdrawn": True,
        "semantic_fields_remain_reviewable": True,
        "exact_pixel_claim_accepted": False,
        "V0R_P1_gate_passed_by_this_receipt": False,
        "V0B_P1_gate_passed_by_this_receipt": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    if claims != required_claims:
        errors.append(error("EVIDENCE_REVOCATION_CLAIMS", subject, "revocation claims must remain fail closed"))
        return set()

    declarations = value.get("revoked_execution_receipts")
    if not isinstance(declarations, list) or not declarations:
        errors.append(error("EVIDENCE_REVOCATION_TARGET", subject, "at least one exact execution receipt target is required"))
        return set()
    revoked: set[Path] = set()
    receipt_root = root.resolve()
    for index, declaration in enumerate(declarations):
        target_subject = f"{subject}.revoked_execution_receipts[{index}]"
        if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256", "receipt_id", "reason_codes"}:
            errors.append(error("EVIDENCE_REVOCATION_TARGET", target_subject, "target fields must match the closed contract"))
            continue
        relative = declaration.get("path")
        target = repository_path(relative) if isinstance(relative, str) else None
        reasons = declaration.get("reason_codes")
        if (
            target is None
            or Path(relative).is_absolute()
            or target.parent != receipt_root
            or target.suffix.lower() != ".json"
            or not target.is_file()
            or normalize_sha256(declaration.get("sha256")) != sha256_file(target)
            or not isinstance(reasons, list)
            or set(reasons) != EVIDENCE_REVOCATION_REASON_CODES
            or len(reasons) != len(EVIDENCE_REVOCATION_REASON_CODES)
        ):
            errors.append(error("EVIDENCE_REVOCATION_TARGET", target_subject, "target path, hash, or reason set is invalid"))
            continue
        target_value = _load(target, target_subject, errors)
        if (
            not isinstance(target_value, dict)
            or target_value.get("receipt_id") != declaration.get("receipt_id")
            or not EXECUTION_RECEIPT_DISCRIMINATOR_FIELDS.intersection(target_value)
            or not V0_FINAL_EVIDENCE_PACKETS.intersection(target_value.get("packet_ids", []))
        ):
            errors.append(error("EVIDENCE_REVOCATION_TARGET", target_subject, "target is not the declared V0 execution receipt"))
            continue
        revoked.add(target.resolve())
    return revoked


def load_execution_receipts(root: Path, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Load only generic execution receipts from the shared evidence directory.

    Only explicitly registered specialized envelopes are excluded, without
    awarding completion credit. Their selected evidence is verified separately.
    Generic discriminators take precedence even on a specialized envelope.
    Unknown document kinds and malformed JSON values fail closed.
    """
    if not root.exists():
        return []
    if not root.is_dir():
        errors.append(error("RECEIPT_ROOT_INVALID", "receipts_root", "receipt root must be a directory"))
        return []
    generic_documents: list[tuple[Path, dict[str, Any]]] = []
    revocation_documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((item for item in root.iterdir() if item.suffix.lower() == ".json"), key=lambda item: item.name):
        subject = f"receipt:{path.name}"
        error_count = len(errors)
        value = _load(path, subject, errors)
        if len(errors) != error_count:
            continue
        if not isinstance(value, dict):
            errors.append(error("RECEIPT_DOCUMENT_SHAPE", subject, "receipt document must be an object"))
            continue
        if not EXECUTION_RECEIPT_DISCRIMINATOR_FIELDS.intersection(value):
            contract = value.get("contract")
            if contract == EVIDENCE_REVOCATION_CONTRACT:
                revocation_documents.append((path, value))
            elif not isinstance(contract, str) or contract not in SPECIALIZED_RECEIPT_ENVELOPES:
                errors.append(error("RECEIPT_DOCUMENT_KIND_UNKNOWN", subject, "unregistered non-generic document"))
            elif (
                type(value.get("schema_version")) is not int
                or value.get("schema_version") != 1
                or value.get("packet_id") != SPECIALIZED_RECEIPT_ENVELOPES[contract]
            ):
                errors.append(error("SPECIALIZED_RECEIPT_ENVELOPE_INVALID", subject, "specialized schema version or packet differs"))
            continue
        generic_documents.append((path, value))

    revoked_paths: set[Path] = set()
    for path, value in revocation_documents:
        targets = _validate_evidence_revocation(value, path, root, errors)
        duplicate_targets = revoked_paths.intersection(targets)
        if duplicate_targets:
            errors.append(error("EVIDENCE_REVOCATION_DUPLICATE_TARGET", f"receipt:{path.name}", str(sorted(map(str, duplicate_targets)))))
        revoked_paths.update(targets)

    receipts: list[dict[str, Any]] = []
    for path, value in generic_documents:
        if path.resolve() in revoked_paths:
            continue
        value = dict(value)
        value["__path__"] = str(path)
        receipts.append(value)
    ids = [item.get("receipt_id") for item in receipts if isinstance(item.get("receipt_id"), str)]
    duplicate = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicate:
        errors.append(error("DUPLICATE_RECEIPT_ID", "receipts_root", str(duplicate)))
    return receipts


def verify_selected_v0b_final_receipt(path: Path) -> dict[str, Any]:
    # Keep the expensive pixel verifier lazy; archived specialized documents
    # never trigger a current-source audit merely by being discovered.
    from code_assist_v1.tools.compile_internal_collaborative_v0b_p1_final_receipt import verify_final_receipt

    return verify_final_receipt(
        path,
        repository_root=REPOSITORY_ROOT,
        allow_receipt_bound_runtime_for_visual_audit=True,
    )


def verify_selected_v0r_reference_receipt(path: Path) -> dict[str, Any]:
    from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt import verify

    return verify(
        path,
        screenshot_root=V0R_REFERENCE_SCREENSHOT_ROOT,
        mask_root=V0R_REFERENCE_MASK_ROOT,
    )


def _validate_v0r_reference_evidence(receipt: dict[str, Any], subject: str) -> list[dict[str, str]]:
    declaration = receipt.get("v0r_reference_receipt")
    if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256", "receipt_id"}:
        return [error("V0R_REFERENCE_EVIDENCE_REQUIRED", subject, "v0r_reference_receipt requires exact path, sha256, receipt_id fields")]
    relative = declaration.get("path")
    path = repository_path(relative) if isinstance(relative, str) else None
    if (
        path is None
        or Path(relative).is_absolute()
        or path != V0R_REFERENCE_RECEIPT_PATH.resolve()
        or not path.is_file()
    ):
        return [error("V0R_REFERENCE_EVIDENCE_PATH_INVALID", subject, "selected V0R receipt path is invalid")]
    try:
        actual_hash = sha256_file(path)
        if (
            normalize_sha256(declaration.get("sha256")) != actual_hash
            or normalize_sha256(receipt.get("artifact_hashes", {}).get(relative)) != actual_hash
            or normalize_sha256(receipt.get("release_candidate_id")) != actual_hash
        ):
            return [error("V0R_REFERENCE_EVIDENCE_HASH_MISMATCH", subject, "generic receipt must bind the immutable V0R hash")]
        selected = _load(path, subject + ".v0r_reference_receipt", [])
        if (
            not isinstance(selected, dict)
            or selected.get("contract") != "code_assist.notion.internal_collaborative_reference_multiuser_receipt.v0r_p1.v1"
            or selected.get("packet_id") != "V0R-P1"
            or selected.get("receipt_id") != declaration.get("receipt_id")
        ):
            return [error("V0R_REFERENCE_EVIDENCE_IDENTITY_MISMATCH", subject, "selected V0R receipt identity differs")]
        report = verify_selected_v0r_reference_receipt(path)
        if (
            report.get("status") != "pass"
            or report.get("error_count") != 0
            or report.get("errors") != []
            or report.get("gate_passed") is not True
            or report.get("exact_reference_gate_passed") is not True
            or report.get("capture_count") != 36
            or report.get("capture_state_count") != 18
            or report.get("viewport_count") != 2
            or report.get("cleanup_complete") is not True
            or normalize_sha256(report.get("receipt_sha256")) != actual_hash
            or sha256_file(path) != actual_hash
        ):
            return [error("V0R_REFERENCE_EVIDENCE_VERIFICATION_FAILED", subject, "fresh V0R verifier did not confirm the bound evidence")]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [error("V0R_REFERENCE_EVIDENCE_VERIFICATION_FAILED", subject, f"verification failed: {exc}")]
    return []


def _validate_v0_final_evidence(receipt: dict[str, Any], subject: str) -> list[dict[str, str]]:
    """Bind a generic V0 closing receipt to the reconstructed final evidence."""
    packet_ids = set(receipt.get("packet_ids", []))
    if packet_ids == {"V0R-P1"}:
        return _validate_v0r_reference_evidence(receipt, subject)
    declaration = receipt.get("v0b_final_receipt")
    if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256", "receipt_id"}:
        return [error("V0_FINAL_EVIDENCE_REQUIRED", subject, "v0b_final_receipt requires exact path, sha256, receipt_id fields")]
    relative = declaration.get("path")
    path = repository_path(relative)
    expected_hash = normalize_sha256(declaration.get("sha256"))
    if (
        path is None
        or not isinstance(relative, str)
        or Path(relative).is_absolute()
        or path.parent != DEFAULT_RECEIPTS_ROOT.resolve()
        or path.suffix.lower() != ".json"
        or not path.is_file()
    ):
        return [error("V0_FINAL_EVIDENCE_PATH_INVALID", subject, "selected evidence must be a JSON file in the receipt root")]
    errors: list[dict[str, str]] = []
    try:
        actual_hash = sha256_file(path)
        if expected_hash != actual_hash or normalize_sha256(receipt["artifact_hashes"].get(relative)) != actual_hash:
            return [error("V0_FINAL_EVIDENCE_HASH_MISMATCH", subject, "declaration and artifact hashes must bind the selected file")]
        selected = _load(path, subject + ".v0b_final_receipt", errors)
        if errors:
            return errors
        if (
            not isinstance(selected, dict)
            or selected.get("contract") != V0B_FINAL_RECEIPT_CONTRACT
            or type(selected.get("schema_version")) is not int
            or selected.get("schema_version") != 1
            or selected.get("packet_id") != "V0B-P1"
            or not isinstance(declaration.get("receipt_id"), str)
            or not declaration["receipt_id"]
            or selected.get("receipt_id") != declaration["receipt_id"]
        ):
            return [error("V0_FINAL_EVIDENCE_IDENTITY_MISMATCH", subject, "selected final receipt identity differs")]
        release = selected.get("release_candidate")
        if not isinstance(release, dict) or normalize_sha256(release.get("source_build_sha256")) != normalize_sha256(receipt.get("release_candidate_id")):
            return [error("V0_FINAL_EVIDENCE_RELEASE_MISMATCH", subject, "generic and specialized release candidates differ")]
        report = verify_selected_v0b_final_receipt(path)
        if isinstance(report, dict):
            report = _inherit_v0b_runtime_compatibility(
                report,
                selected_receipt=selected,
                selected_path=path,
            )
        exact_pixel_pass_count = report.get("exact_pixel_pass_count") if isinstance(report, dict) else None
        if (
            not isinstance(report, dict)
            or report.get("contract") != "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_audit.v1"
            or report.get("status") != "pass"
            or report.get("error_count") != 0
            or report.get("errors") != []
            or report.get("gate_passed") is not True
            or report.get("core_test_count") != 21
            or report.get("performance_run_count") != 2
            or report.get("visual_similarity_pass_count") != 36
            or type(exact_pixel_pass_count) is not int
            or not 0 <= exact_pixel_pass_count <= 36
            or report.get("accessibility_pass_count") != 36
            or report.get("receipt_id") != declaration["receipt_id"]
            or normalize_sha256(report.get("receipt_sha256")) != actual_hash
            or sha256_file(path) != actual_hash
        ):
            return [error("V0_FINAL_EVIDENCE_VERIFICATION_FAILED", subject, "fresh final verifier did not confirm the bound evidence")]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [error("V0_FINAL_EVIDENCE_VERIFICATION_FAILED", subject, f"verification failed: {exc}")]
    return []


def _validate_target_file_binding(
    declaration: Any,
    *,
    receipt: Mapping[str, Any],
    subject: str,
    exact_path: Path | None = None,
) -> tuple[Path | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256"}:
        return None, [
            error(
                "TARGET_EVIDENCE_BINDING_INVALID",
                subject,
                "exact path and sha256 fields are required",
            )
        ]
    relative = declaration.get("path")
    path = repository_path(relative) if isinstance(relative, str) else None
    if (
        path is None
        or not isinstance(relative, str)
        or Path(relative).is_absolute()
        or not path.is_file()
        or path.suffix.lower() != ".json"
    ):
        return None, [
            error(
                "TARGET_EVIDENCE_PATH_INVALID",
                subject,
                "repository-relative existing JSON evidence is required",
            )
        ]
    if exact_path is not None:
        if path != exact_path.resolve(strict=True):
            return None, [
                error(
                    "TARGET_POLICY_PATH_INVALID",
                    subject,
                    "the reviewed O1B target policy is required",
                )
            ]
    else:
        try:
            path.relative_to(TARGET_EVIDENCE_ROOT.resolve(strict=True))
        except (OSError, ValueError):
            return None, [
                error(
                    "TARGET_EVIDENCE_PATH_INVALID",
                    subject,
                    "target evidence must stay in the approved repository evidence root",
                )
            ]
    try:
        actual = sha256_file(path)
    except OSError as exc:
        return None, [error("TARGET_EVIDENCE_READ_FAILED", subject, str(exc))]
    artifact_hashes = receipt.get("artifact_hashes")
    if (
        normalize_sha256(declaration.get("sha256")) != actual
        or not isinstance(artifact_hashes, dict)
        or normalize_sha256(artifact_hashes.get(relative)) != actual
    ):
        errors.append(
            error(
                "TARGET_EVIDENCE_HASH_MISMATCH",
                subject,
                "declaration and generic receipt must bind the current evidence bytes",
            )
        )
    return path, errors


def _validate_target_gate_evidence(
    receipt: dict[str, Any],
    subject: str,
) -> list[dict[str, str]]:
    """Freshly reconstruct O1B/Q0 evidence instead of trusting receipt labels."""

    packet_set = set(receipt.get("packet_ids", []))
    selected_packets = packet_set & TARGET_GATE_PACKETS
    if len(selected_packets) != 1 or packet_set != selected_packets:
        return [
            error(
                "TARGET_GATE_PACKET_SET_INVALID",
                subject + ".packet_ids",
                "a target gate receipt must bind exactly one of O1B-P1 or Q0-P1",
            )
        ]
    packet_id = next(iter(selected_packets))
    contract = TARGET_GATE_CONTRACTS[packet_id]
    errors: list[dict[str, str]] = []
    if (
        receipt.get("lane_id") != contract["lane_id"]
        or receipt.get("realm_id") != contract["realm_id"]
        or receipt.get("environment_class") != contract["environment_class"]
        or receipt.get("matrix_row_ids") != contract["matrix_row_ids"]
        or receipt.get("scenario_ids") != contract["scenario_ids"]
    ):
        errors.append(
            error(
                "TARGET_GATE_IDENTITY_INVALID",
                subject,
                "lane, realm, environment, matrix, and scenario bindings must match the target gate",
            )
        )
    isolation = receipt.get("isolation_attestation")
    if (
        receipt.get("target_operations_executed_by_verifier") is not False
        or receipt.get("product_complete") is not False
        or not isinstance(isolation, dict)
        or isolation.get("operator_actions_executed_by_verifier") is not False
        or isolation.get("pytest_invoked_by_verifier") is not False
    ):
        errors.append(
            error(
                "TARGET_GATE_FALSE_GREEN",
                subject,
                "the verifier must execute neither pytest nor target actions and cannot claim product completion",
            )
        )

    declaration = receipt.get("o1b_target_evidence")
    expected_declaration_keys = {
        "policy",
        "approval",
        "handoff",
        "evidence_index",
        "evidence_root",
        "target_receipt",
    }
    if not isinstance(declaration, dict) or set(declaration) != expected_declaration_keys:
        return errors + [
            error(
                "TARGET_EVIDENCE_SET_REQUIRED",
                subject + ".o1b_target_evidence",
                "complete O1B policy, approval, handoff, index, evidence root, and receipt bindings are required",
            )
        ]

    paths: dict[str, Path] = {}
    binding_names = {
        "policy": "policy",
        "approval": "approval",
        "handoff": "handoff",
        "evidence_index": "evidence_index",
        "target_receipt": "receipt",
    }
    for declaration_name, path_name in binding_names.items():
        path, binding_errors = _validate_target_file_binding(
            declaration.get(declaration_name),
            receipt=receipt,
            subject=f"{subject}.o1b_target_evidence.{declaration_name}",
            exact_path=TARGET_POLICY_PATH if declaration_name == "policy" else None,
        )
        errors.extend(binding_errors)
        if path is not None:
            paths[path_name] = path

    evidence_root_relative = declaration.get("evidence_root")
    evidence_root = (
        repository_path(evidence_root_relative)
        if isinstance(evidence_root_relative, str)
        else None
    )
    try:
        evidence_root_valid = (
            evidence_root is not None
            and not Path(evidence_root_relative).is_absolute()
            and evidence_root.is_dir()
            and evidence_root.is_relative_to(TARGET_EVIDENCE_ROOT.resolve(strict=True))
        )
    except (OSError, TypeError, ValueError):
        evidence_root_valid = False
    if not evidence_root_valid:
        errors.append(
            error(
                "TARGET_EVIDENCE_ROOT_INVALID",
                subject + ".o1b_target_evidence.evidence_root",
                "an existing repository evidence directory is required",
            )
        )

    if "policy" in paths:
        try:
            policy_sha256 = sha256_file(paths["policy"])
            policy = load_json(paths["policy"])
            trust = policy.get("trust_anchor_policy") if isinstance(policy, dict) else None
            authorized_release = (
                normalize_sha256(trust.get("authorized_release_candidate_sha256"))
                if isinstance(trust, dict)
                else None
            )
            if (
                normalize_sha256(receipt.get("realm_config_fingerprint"))
                != policy_sha256
                or normalize_sha256(receipt.get("release_candidate_id"))
                != authorized_release
                or not isinstance(trust, dict)
                or trust.get("status") != "bound"
            ):
                errors.append(
                    error(
                        "TARGET_POLICY_BINDING_MISMATCH",
                        subject,
                        "receipt realm and release candidate must match the bound O1B policy",
                    )
                )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(error("TARGET_POLICY_READ_FAILED", subject, str(exc)))
            policy_sha256 = None
    else:
        policy_sha256 = None

    required_paths = {"policy", "approval", "handoff", "evidence_index", "receipt"}
    if (
        not errors
        and required_paths.issubset(paths)
        and evidence_root is not None
        and policy_sha256 is not None
    ):
        from code_assist_v1.tools.verify_internal_collaborative_target_restore import (
            verify as verify_target_restore,
        )

        try:
            report = verify_target_restore(
                paths["policy"],
                approval_path=paths["approval"],
                handoff_path=paths["handoff"],
                evidence_index_path=paths["evidence_index"],
                evidence_root=evidence_root,
                receipt_path=paths["receipt"],
                expected_policy_sha256=policy_sha256,
                require_execution=True,
            )
            if not (
                report.get("status") == "pass"
                and report.get("error_count") == 0
                and report.get("errors") == []
                and report.get("gate_passed") is True
                and report.get("execution_required_mode") is True
                and report.get("product_complete") is False
            ):
                errors.append(
                    error(
                        "TARGET_EVIDENCE_VERIFICATION_FAILED",
                        subject,
                        "fresh O1B target verifier did not confirm the bound evidence",
                    )
                )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            errors.append(
                error(
                    "TARGET_EVIDENCE_VERIFICATION_FAILED",
                    subject,
                    f"verification failed: {exc}",
                )
            )

    if packet_id == "Q0-P1":
        q0_path, q0_errors = _validate_target_file_binding(
            receipt.get("q0_report"),
            receipt=receipt,
            subject=subject + ".q0_report",
        )
        q0_attestation_path, q0_attestation_errors = _validate_target_file_binding(
            receipt.get("q0_attestation"),
            receipt=receipt,
            subject=subject + ".q0_attestation",
        )
        errors.extend(q0_errors)
        errors.extend(q0_attestation_errors)
        if (
            q0_path is not None
            and q0_attestation_path is not None
            and not q0_errors
            and not q0_attestation_errors
            and required_paths.issubset(paths)
            and policy_sha256 is not None
        ):
            from code_assist_v1.notion_collaboration.q0_validation import (
                Q0EvidenceError,
                validate_q0_report,
            )
            from code_assist_v1.tools.q0_target_attestation import (
                load_q0_json_document,
                validate_q0_target_attestation,
            )

            try:
                q0_value = load_q0_json_document(q0_path)
                q0_attestation = load_q0_json_document(q0_attestation_path)
                result = validate_q0_report(q0_value, require_target_realm=True)
                if result.get("target_gate_eligible") is not True:
                    raise Q0EvidenceError("Q0 target gate eligibility missing")
                policy_value = load_json(paths["policy"])
                approval_value = load_json(paths["approval"])
                target_receipt_value = load_json(paths["receipt"])
                if not all(
                    isinstance(value, dict)
                    for value in (
                        policy_value,
                        approval_value,
                        target_receipt_value,
                    )
                ):
                    raise Q0EvidenceError("verified O1B evidence objects required")
                attestation_result = validate_q0_target_attestation(
                    q0_attestation,
                    report=q0_value,
                    q0_report_sha256=sha256_file(q0_path),
                    policy=policy_value,
                    approval=approval_value,
                    target_receipt=target_receipt_value,
                    target_receipt_sha256=sha256_file(paths["receipt"]),
                    expected_release_candidate_id=str(
                        receipt.get("release_candidate_id", "")
                    ),
                    expected_realm_config_fingerprint=str(
                        receipt.get("realm_config_fingerprint", "")
                    ),
                )
                if attestation_result.get("target_gate_eligible") is not True:
                    raise Q0EvidenceError("Q0 target attestation eligibility missing")
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    error(
                        "Q0_TARGET_ATTESTATION_VERIFICATION_FAILED",
                        subject + ".q0_attestation",
                        str(exc),
                    )
                )
    elif "q0_report" in receipt or "q0_attestation" in receipt:
        errors.append(
            error(
                "TARGET_GATE_UNEXPECTED_Q0_EVIDENCE",
                subject + ".q0_attestation",
                "O1B restore receipt must not claim Q0 load evidence or attestation",
            )
        )
    return errors


def _validate_realm_promotion_evidence(
    receipt: dict[str, Any],
    subject: str,
) -> list[dict[str, str]]:
    """Freshly reconstruct the three L9 operator stages from one bound manifest."""

    packet_set = set(receipt.get("packet_ids", []))
    if packet_set != PROMOTION_GATE_PACKETS:
        return [
            error(
                "PROMOTION_GATE_PACKET_SET_INVALID",
                subject + ".packet_ids",
                "L9 promotion must bind exactly R0A-P1, R0B-P1, and R1-P1",
            )
        ]

    errors: list[dict[str, str]] = []
    if (
        receipt.get("lane_id") != "L9-REALM-PROMOTION"
        or receipt.get("realm_id") != "production_canary"
        or receipt.get("environment_class")
        != "operator_action_receipt_verification"
        or receipt.get("matrix_row_ids")
        != ["ICN-OPS-002", "ROLL-001", "OBS-001", "PILOT-001"]
        or receipt.get("scenario_ids") != []
        or receipt.get("operator_action_receipt_count") != 3
        or not isinstance(receipt.get("deployment_generation_id"), str)
        or not receipt["deployment_generation_id"]
    ):
        errors.append(
            error(
                "PROMOTION_GATE_IDENTITY_INVALID",
                subject,
                "L9 lane, packets, realm, matrix, and action denominator must match",
            )
        )

    isolation = receipt.get("isolation_attestation")
    if (
        receipt.get("full_rollout_enabled_by_verifier") is not False
        or receipt.get("product_complete") is not False
        or not isinstance(isolation, dict)
        or isolation.get("operator_actions_executed_by_verifier") is not False
        or isolation.get("pytest_invoked_by_verifier") is not False
    ):
        errors.append(
            error(
                "PROMOTION_GATE_FALSE_GREEN",
                subject,
                "the verifier must execute no operator action, pytest, or rollout",
            )
        )

    declaration = receipt.get("realm_promotion_evidence")
    if not isinstance(declaration, dict) or set(declaration) != {
        "path",
        "sha256",
        "manifest_id",
    }:
        return errors + [
            error(
                "PROMOTION_EVIDENCE_REQUIRED",
                subject + ".realm_promotion_evidence",
                "exact promotion manifest path, sha256, and identity are required",
            )
        ]

    relative = declaration.get("path")
    manifest_path = repository_path(relative) if isinstance(relative, str) else None
    try:
        path_valid = (
            manifest_path is not None
            and not Path(relative).is_absolute()
            and manifest_path.is_file()
            and manifest_path.suffix.lower() == ".json"
            and manifest_path.is_relative_to(
                PROMOTION_EVIDENCE_ROOT.resolve(strict=True)
            )
        )
    except (OSError, TypeError, ValueError):
        path_valid = False
    if not path_valid or manifest_path is None or not isinstance(relative, str):
        return errors + [
            error(
                "PROMOTION_EVIDENCE_PATH_INVALID",
                subject + ".realm_promotion_evidence.path",
                "promotion manifest must stay in the approved promotion evidence root",
            )
        ]

    try:
        manifest_sha256 = sha256_file(manifest_path)
    except OSError as exc:
        return errors + [
            error(
                "PROMOTION_EVIDENCE_READ_FAILED",
                subject + ".realm_promotion_evidence",
                str(exc),
            )
        ]
    artifact_hashes = receipt.get("artifact_hashes")
    if (
        normalize_sha256(declaration.get("sha256")) != manifest_sha256
        or not isinstance(artifact_hashes, dict)
        or normalize_sha256(artifact_hashes.get(relative)) != manifest_sha256
    ):
        errors.append(
            error(
                "PROMOTION_EVIDENCE_HASH_MISMATCH",
                subject + ".realm_promotion_evidence",
                "declaration and generic receipt must bind the manifest bytes",
            )
        )

    try:
        manifest = load_json(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return errors + [
            error(
                "PROMOTION_EVIDENCE_READ_FAILED",
                subject + ".realm_promotion_evidence",
                str(exc),
            )
        ]
    if (
        not isinstance(manifest, dict)
        or manifest.get("contract")
        != "code_assist.notion.internal_collaborative_realm_promotion_manifest.v1"
        or not isinstance(declaration.get("manifest_id"), str)
        or not declaration["manifest_id"]
        or manifest.get("manifest_id") != declaration["manifest_id"]
    ):
        errors.append(
            error(
                "PROMOTION_EVIDENCE_IDENTITY_MISMATCH",
                subject + ".realm_promotion_evidence",
                "bound promotion manifest identity differs",
            )
        )

    if errors:
        return errors

    from code_assist_v1.notion_collaboration.realm_promotion import (
        PROMOTION_STAGES,
        verify_realm_promotion_manifest,
    )

    try:
        report, exit_code = verify_realm_promotion_manifest(
            manifest_path,
            expected_release_candidate_id=receipt["release_candidate_id"],
            expected_realm_id=receipt["realm_id"],
            expected_realm_config_fingerprint=receipt[
                "realm_config_fingerprint"
            ],
            expected_source_manifest_sha256=receipt["source_manifest_sha256"],
            expected_validation_manifest_sha256=receipt[
                "validation_manifest_sha256"
            ],
            expected_schema_catalog_sha256=receipt["schema_catalog_sha256"],
            expected_sbe_handoff_bindings={
                "agents_md_sha256": receipt["agents_md_sha256"],
                "current_goal_observation_sha256": receipt[
                    "goal_observation_sha256"
                ],
                "current_schema_lineage_sha256": receipt[
                    "current_schema_lineage_sha256"
                ],
            },
        )
        if not (
            exit_code == 0
            and report.get("status") == "pass"
            and report.get("gate_eligible") is True
            and report.get("error_count") == 0
            and report.get("errors") == []
            and report.get("manifest_id") == declaration["manifest_id"]
            and report.get("release_candidate_id")
            == receipt["release_candidate_id"]
            and report.get("deployment_generation_id")
            == receipt["deployment_generation_id"]
            and report.get("realm_id") == receipt["realm_id"]
            and report.get("realm_config_fingerprint")
            == receipt["realm_config_fingerprint"]
            and report.get("verified_stages") == list(PROMOTION_STAGES)
            and report.get("verified_action_receipt_count") == 3
            and report.get("artifact_hashes") == artifact_hashes
            and report.get("operator_actions_executed_by_verifier") is False
            and report.get("live_roots_accessed_by_verifier") is False
            and report.get("pytest_invoked_by_verifier") is False
            and report.get("full_rollout_enabled_by_verifier") is False
            and report.get("product_complete") is False
        ):
            errors.append(
                error(
                    "PROMOTION_EVIDENCE_VERIFICATION_FAILED",
                    subject,
                    "fresh promotion verifier did not confirm the bound evidence",
                )
            )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        errors.append(
            error(
                "PROMOTION_EVIDENCE_VERIFICATION_FAILED",
                subject,
                f"verification failed: {exc}",
            )
        )
    return errors


def _load_z0_bound_receipt(
    declaration: Any,
    *,
    subject: str,
    allowed_root: Path,
    identity_field: str = "receipt_id",
) -> tuple[Path | None, dict[str, Any] | None, list[dict[str, str]]]:
    """Load one exact immutable receipt binding below its declared evidence root."""

    if not isinstance(declaration, dict) or set(declaration) != {
        "path",
        "sha256",
        "receipt_id",
    }:
        return None, None, [
            error(
                "Z0_EVIDENCE_BINDING_INVALID",
                subject,
                "exact path, sha256, and receipt_id fields are required",
            )
        ]
    relative = declaration.get("path")
    path = repository_path(relative) if isinstance(relative, str) else None
    try:
        path_valid = (
            path is not None
            and not Path(relative).is_absolute()
            and path.is_file()
            and path.suffix.lower() == ".json"
            and path.is_relative_to(allowed_root.resolve(strict=True))
        )
    except (OSError, TypeError, ValueError):
        path_valid = False
    if not path_valid or path is None:
        return None, None, [
            error(
                "Z0_EVIDENCE_PATH_INVALID",
                subject + ".path",
                "bound receipt must be an existing repository JSON below the exact evidence root",
            )
        ]
    try:
        actual_sha256 = sha256_file(path)
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, None, [
            error("Z0_EVIDENCE_READ_FAILED", subject, str(exc))
        ]
    if normalize_sha256(declaration.get("sha256")) != actual_sha256:
        return path, None, [
            error(
                "Z0_EVIDENCE_HASH_MISMATCH",
                subject + ".sha256",
                "bound receipt bytes differ",
            )
        ]
    if (
        not isinstance(value, dict)
        or not isinstance(declaration.get("receipt_id"), str)
        or not declaration["receipt_id"]
        or value.get(identity_field) != declaration["receipt_id"]
    ):
        return path, None, [
            error(
                "Z0_EVIDENCE_IDENTITY_MISMATCH",
                subject + ".receipt_id",
                "bound receipt identity differs",
            )
        ]
    return path, value, []


def verify_selected_z0_release_candidate(path: Path) -> tuple[dict[str, Any], int]:
    from code_assist_v1.notion_collaboration.release_candidate import (
        verify_release_candidate_manifest,
    )

    return verify_release_candidate_manifest(path)


def _validate_z0_suite_receipt(
    value: dict[str, Any],
    *,
    lane_id: str,
    manifest: Mapping[str, Any],
    ladder: Mapping[str, Any],
    subject: str,
) -> list[dict[str, str]]:
    """Reconstruct one of the exact full-Python or six Node final-suite runs."""

    errors: list[dict[str, str]] = []
    receipt_schema = _load(DEFAULT_RECEIPT_SCHEMA, subject + ".schema", errors)
    if not isinstance(receipt_schema, dict):
        return errors or [
            error("Z0_SUITE_SCHEMA_INVALID", subject, "execution receipt schema is unavailable")
        ]
    for item in JsonSchemaValidator(receipt_schema).validate(value):
        errors.append(
            error(
                f"Z0_SUITE_{item['code']}",
                subject + item["subject"][1:],
                item["message"],
            )
        )
    required_fields = set(receipt_schema.get("required", [])) | {
        "contract",
        "collection_errors",
        "deployment_generation_id",
        "release_candidate_manifest",
        "sbe_contract_audit_input_sha256",
    }
    if set(value) != required_fields:
        errors.append(
            error(
                "Z0_SUITE_FIELDS_INVALID",
                subject,
                "supporting suite receipt fields must match the closed contract",
            )
        )

    expected_lane: Mapping[str, Any] | None = None
    expected_rung: Mapping[str, Any] | None = None
    for rung in ladder.get("rungs", []):
        if not isinstance(rung, dict):
            continue
        for lane in rung.get("lanes", []):
            if isinstance(lane, dict) and lane.get("id") == lane_id:
                expected_lane = lane
                expected_rung = rung
                break
        if expected_lane is not None:
            break
    if expected_lane is None or expected_rung is None:
        errors.append(error("Z0_SUITE_LANE_UNKNOWN", subject + ".lane_id", lane_id))
        return errors

    exact_bindings = {
        "release_candidate_id": manifest.get("release_candidate_id"),
        "source_manifest_sha256": manifest.get("source_manifest_sha256"),
        "validation_manifest_sha256": manifest.get("validation_manifest_sha256"),
        "schema_catalog_sha256": manifest.get("schema_catalog_sha256"),
        "current_schema_lineage_sha256": manifest.get("current_schema_lineage_sha256"),
        "agents_md_sha256": manifest.get("agents_md_sha256"),
        "goal_observation_sha256": manifest.get("goal_observation_sha256"),
        "goal_thread_id": manifest.get("goal_thread_id"),
        "cross_catalog_ownership_snapshot_sha256": manifest.get(
            "cross_catalog_ownership_snapshot_sha256"
        ),
        "sbe_contract_audit_input_sha256": manifest.get(
            "sbe_contract_audit_input_sha256"
        ),
        "realm_config_fingerprint": manifest.get(
            "synthetic_realm_config_fingerprint"
        ),
        "deployment_generation_id": manifest.get("deployment_generation_id"),
    }
    for key, expected in exact_bindings.items():
        if value.get(key) != expected:
            errors.append(
                error(
                    "Z0_SUITE_BINDING_DRIFT",
                    subject + "." + key,
                    "supporting suite and final manifest bindings differ",
                )
            )

    if (
        value.get("schema_version") != 1
        or value.get("contract") != Z0_FINAL_SUITE_RECEIPT_CONTRACT
        or value.get("rung_id") != expected_rung.get("id")
        or value.get("lane_id") != lane_id
        or value.get("packet_ids") != []
        or value.get("matrix_row_ids") != []
        or value.get("scenario_ids") != []
        or value.get("evidence_type") != "final_suite"
        or value.get("mode") != "gate"
        or value.get("realm_id") != "synthetic_isolated"
        or value.get("environment_class") != "synthetic_isolated_final_suite"
        or value.get("working_directory") != "."
        or value.get("argv") != expected_lane.get("argv")
    ):
        errors.append(
            error(
                "Z0_SUITE_IDENTITY_INVALID",
                subject,
                "suite contract, rung, lane, realm, working directory, or exact argv differs",
            )
        )

    passed = value.get("passed")
    if (
        value.get("result") != "passed"
        or value.get("exit_code") != 0
        or value.get("exit_code_signed") != 0
        or value.get("exit_code_hex") != "0x00000000"
        or type(passed) is not int
        or passed <= 0
        or value.get("failed") != 0
        or value.get("errors") != []
        or value.get("skipped") != 0
        or value.get("xfailed") != 0
        or value.get("collection_errors") != 0
        or value.get("attempt") != 1
        or value.get("retry_class") != "none"
    ):
        errors.append(
            error(
                "Z0_SUITE_RESULT_INVALID",
                subject,
                "final suite must pass with positive tests and zero failures, skips, xfails, collection errors, or retries",
            )
        )
    if value.get("source_pre_sha256") != value.get("source_post_sha256"):
        errors.append(
            error(
                "Z0_SUITE_SOURCE_DRIFT",
                subject,
                "bound sources changed during the supporting suite",
            )
        )
    isolation = value.get("isolation_attestation")
    cleanup = value.get("owned_process_cleanup")
    cleanup_attestation = value.get("cleanup_attestation")
    redaction = value.get("redaction_summary")
    if (
        not isinstance(isolation, dict)
        or isolation.get("shell_used") is not False
        or isolation.get("live_roots_accessed") is not False
        or isolation.get("operator_actions_executed") is not False
        or isolation.get("selected_command_only") is not True
        or not isinstance(cleanup, dict)
        or cleanup.get("complete") is not True
        or cleanup.get("unrelated_processes_targeted") is not False
        or not isinstance(cleanup_attestation, dict)
        or cleanup_attestation.get("source_hashes_unchanged") is not True
        or not isinstance(redaction, dict)
        or redaction.get("raw_stdout_recorded") is not False
        or redaction.get("raw_stderr_recorded") is not False
        or redaction.get("private_content_recorded") is not False
    ):
        errors.append(
            error(
                "Z0_SUITE_ATTESTATION_INVALID",
                subject,
                "isolation, cleanup, and redaction attestations must be exact",
            )
        )
    artifact_hashes = value.get("artifact_hashes")
    if (
        not isinstance(artifact_hashes, dict)
        or normalize_sha256(artifact_hashes.get("stdout_sha256")) is None
        or normalize_sha256(artifact_hashes.get("stderr_sha256")) is None
    ):
        errors.append(
            error(
                "Z0_SUITE_ARTIFACT_HASH_INVALID",
                subject + ".artifact_hashes",
                "redacted stream hashes are required",
            )
        )
    release_declaration = value.get("release_candidate_manifest")
    if not isinstance(release_declaration, dict) or set(release_declaration) != {
        "path",
        "sha256",
        "manifest_id",
    }:
        errors.append(
            error(
                "Z0_SUITE_RELEASE_EVIDENCE_REQUIRED",
                subject + ".release_candidate_manifest",
                "exact release manifest path, sha256, and identity are required",
            )
        )
        return errors
    release_path, release_manifest, release_errors = _load_z0_bound_receipt(
        {
            "path": release_declaration.get("path"),
            "sha256": release_declaration.get("sha256"),
            "receipt_id": release_declaration.get("manifest_id"),
        },
        subject=subject + ".release_candidate_manifest",
        allowed_root=PROMOTION_EVIDENCE_ROOT,
        identity_field="manifest_id",
    )
    errors.extend(release_errors)
    if release_path is not None and release_manifest is not None:
        relative_release = release_path.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
        if (
            not isinstance(artifact_hashes, dict)
            or normalize_sha256(artifact_hashes.get(relative_release))
            != sha256_file(release_path)
        ):
            errors.append(
                error(
                    "Z0_SUITE_RELEASE_HASH_MISMATCH",
                    subject + ".artifact_hashes",
                    "supporting receipt must bind the release manifest bytes",
                )
            )
        try:
            release_report, release_exit = verify_selected_z0_release_candidate(
                release_path
            )
            if not (
                release_exit == 0
                and release_report.get("status") == "pass"
                and release_report.get("error_count") == 0
                and release_report.get("errors") == []
                and release_report.get("manifest_id")
                == release_declaration["manifest_id"]
                and release_report.get("release_candidate_id")
                == manifest.get("release_candidate_id")
                and release_report.get("deployment_generation_id")
                == manifest.get("deployment_generation_id")
                and normalize_sha256(release_report.get("manifest_sha256"))
                == sha256_file(release_path)
                and release_report.get("production_or_runtime_root_accessed")
                is False
                and release_report.get("product_complete") is False
            ):
                errors.append(
                    error(
                        "Z0_SUITE_RELEASE_VERIFICATION_FAILED",
                        subject + ".release_candidate_manifest",
                        "fresh release-candidate reconstruction failed",
                    )
                )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            errors.append(
                error(
                    "Z0_SUITE_RELEASE_VERIFICATION_FAILED",
                    subject + ".release_candidate_manifest",
                    f"verification failed: {exc}",
                )
            )
    return errors


def _validate_z0_final_evidence(
    receipt: dict[str, Any],
    subject: str,
    *,
    ladder: Mapping[str, Any],
    sbe_report: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Freshly reconstruct the seven final suites and the promoted generation."""

    if set(receipt.get("packet_ids", [])) != {"Z0-P1"} or receipt.get(
        "packet_ids"
    ) != ["Z0-P1"]:
        return [
            error(
                "Z0_GATE_PACKET_SET_INVALID",
                subject + ".packet_ids",
                "Z0 final evidence must bind exactly Z0-P1",
            )
        ]
    errors: list[dict[str, str]] = []
    if (
        receipt.get("lane_id") != "L9-REALM-PROMOTION"
        or receipt.get("realm_id") != "production_canary"
        or receipt.get("environment_class")
        != "strict_completion_receipt_verification"
        or receipt.get("matrix_row_ids") != []
        or receipt.get("scenario_ids") != []
        or not isinstance(receipt.get("deployment_generation_id"), str)
        or not receipt["deployment_generation_id"]
        or receipt.get("final_suite_receipt_count") != len(
            Z0_REQUIRED_SUITE_LANES
        )
    ):
        errors.append(
            error(
                "Z0_GATE_IDENTITY_INVALID",
                subject,
                "Z0 lane, realm, environment, empty closure sets, generation, and suite denominator must match",
            )
        )
    isolation = receipt.get("isolation_attestation")
    if (
        receipt.get("full_rollout_enabled_by_verifier") is not False
        or receipt.get("product_complete") is not False
        or receipt.get("active_shared_goal_complete") is not False
        or not isinstance(isolation, dict)
        or isolation.get("operator_actions_executed_by_verifier") is not False
        or isolation.get("pytest_invoked_by_verifier") is not False
    ):
        errors.append(
            error(
                "Z0_GATE_FALSE_GREEN",
                subject,
                "the final verifier must execute no suite, operator action, rollout, or completion claim",
            )
        )

    declaration = receipt.get("z0_final_evidence")
    if not isinstance(declaration, dict) or set(declaration) != {
        "path",
        "sha256",
        "manifest_id",
    }:
        return errors + [
            error(
                "Z0_FINAL_EVIDENCE_REQUIRED",
                subject + ".z0_final_evidence",
                "exact final manifest path, sha256, and identity are required",
            )
        ]
    manifest_path, manifest, binding_errors = _load_z0_bound_receipt(
        {
            "path": declaration.get("path"),
            "sha256": declaration.get("sha256"),
            "receipt_id": declaration.get("manifest_id"),
        },
        subject=subject + ".z0_final_evidence",
        allowed_root=Z0_FINAL_EVIDENCE_ROOT,
        identity_field="manifest_id",
    )
    errors.extend(binding_errors)
    if manifest is None or manifest_path is None:
        return errors
    if set(manifest) != Z0_FINAL_MANIFEST_FIELDS:
        errors.append(
            error(
                "Z0_FINAL_MANIFEST_FIELDS_INVALID",
                subject + ".z0_final_evidence",
                "final manifest fields must match the closed contract",
            )
        )
        return errors
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract") != Z0_FINAL_MANIFEST_CONTRACT
        or manifest.get("manifest_id") != declaration.get("manifest_id")
        or manifest.get("claims") != Z0_FINAL_MANIFEST_CLAIMS
    ):
        errors.append(
            error(
                "Z0_FINAL_MANIFEST_IDENTITY_INVALID",
                subject + ".z0_final_evidence",
                "manifest contract, identity, or fail-closed claims differ",
            )
        )

    direct_bindings = {
        "release_candidate_id": receipt.get("release_candidate_id"),
        "deployment_generation_id": receipt.get("deployment_generation_id"),
        "source_manifest_sha256": receipt.get("source_manifest_sha256"),
        "validation_manifest_sha256": receipt.get("validation_manifest_sha256"),
        "schema_catalog_sha256": receipt.get("schema_catalog_sha256"),
        "current_schema_lineage_sha256": receipt.get(
            "current_schema_lineage_sha256"
        ),
        "agents_md_sha256": receipt.get("agents_md_sha256"),
        "goal_observation_sha256": receipt.get("goal_observation_sha256"),
        "goal_thread_id": receipt.get("goal_thread_id"),
        "cross_catalog_ownership_snapshot_sha256": receipt.get(
            "cross_catalog_ownership_snapshot_sha256"
        ),
        "production_realm_config_fingerprint": receipt.get(
            "realm_config_fingerprint"
        ),
    }
    for key, expected in direct_bindings.items():
        if manifest.get(key) != expected:
            errors.append(
                error(
                    "Z0_FINAL_BINDING_DRIFT",
                    subject + ".z0_final_evidence." + key,
                    "generic receipt and final manifest differ",
                )
            )
    synthetic_fingerprint = normalize_sha256(
        manifest.get("synthetic_realm_config_fingerprint")
    )
    production_fingerprint = normalize_sha256(
        manifest.get("production_realm_config_fingerprint")
    )
    if synthetic_fingerprint is None or production_fingerprint is None:
        errors.append(
            error(
                "Z0_FINAL_REALM_BINDING_INVALID",
                subject + ".z0_final_evidence",
                "synthetic and production realm fingerprints must be SHA-256 values",
            )
        )
    current_sbe_input = (
        normalize_sha256(sbe_report.get("input_sha256"))
        if isinstance(sbe_report, Mapping)
        else None
    )
    if (
        not isinstance(sbe_report, Mapping)
        or sbe_report.get("status") != "pass"
        or sbe_report.get("error_count") != 0
        or current_sbe_input is None
        or manifest.get("sbe_contract_audit_input_sha256") != current_sbe_input
        or receipt.get("sbe_contract_audit_input_sha256") != current_sbe_input
    ):
        errors.append(
            error(
                "Z0_FINAL_SBE_AUDIT_INVALID",
                subject + ".sbe_contract_audit_input_sha256",
                "current zero-error SBE contract audit binding is required",
            )
        )

    artifact_hashes = receipt.get("artifact_hashes")
    relative_manifest = manifest_path.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    if (
        not isinstance(artifact_hashes, dict)
        or artifact_hashes
        != {relative_manifest: sha256_file(manifest_path)}
    ):
        errors.append(
            error(
                "Z0_FINAL_ARTIFACT_BINDING_INVALID",
                subject + ".artifact_hashes",
                "generic receipt must bind only the selected final manifest",
            )
        )

    suite_bindings = manifest.get("suite_receipts")
    if not isinstance(suite_bindings, list) or len(suite_bindings) != len(
        Z0_REQUIRED_SUITE_LANES
    ):
        errors.append(
            error(
                "Z0_SUITE_SET_INVALID",
                subject + ".z0_final_evidence.suite_receipts",
                "one exact full-Python and six Node receipt bindings are required",
            )
        )
    else:
        by_lane: dict[str, Any] = {}
        for index, item in enumerate(suite_bindings):
            if not isinstance(item, dict) or set(item) != {
                "lane_id",
                "path",
                "sha256",
                "receipt_id",
            }:
                errors.append(
                    error(
                        "Z0_SUITE_BINDING_INVALID",
                        f"{subject}.z0_final_evidence.suite_receipts[{index}]",
                        "lane_id plus exact immutable receipt binding is required",
                    )
                )
                continue
            lane_id = item.get("lane_id")
            if not isinstance(lane_id, str) or lane_id in by_lane:
                errors.append(
                    error(
                        "Z0_SUITE_LANE_DUPLICATE",
                        f"{subject}.z0_final_evidence.suite_receipts[{index}].lane_id",
                        str(lane_id),
                    )
                )
                continue
            by_lane[lane_id] = item
        if set(by_lane) != set(Z0_REQUIRED_SUITE_LANES):
            errors.append(
                error(
                    "Z0_SUITE_SET_INVALID",
                    subject + ".z0_final_evidence.suite_receipts",
                    "suite lane set differs from the full-Python plus six Node contract",
                )
            )
        else:
            seen_paths: set[Path] = set()
            seen_receipt_ids: set[str] = set()
            for lane_id in Z0_REQUIRED_SUITE_LANES:
                item = by_lane[lane_id]
                path, suite_receipt, item_errors = _load_z0_bound_receipt(
                    {key: item[key] for key in ("path", "sha256", "receipt_id")},
                    subject=subject + ".z0_final_evidence.suite_receipts." + lane_id,
                    allowed_root=Z0_FINAL_EVIDENCE_ROOT,
                )
                errors.extend(item_errors)
                if path is None or suite_receipt is None:
                    continue
                if path in seen_paths or item["receipt_id"] in seen_receipt_ids:
                    errors.append(
                        error(
                            "Z0_SUITE_RECEIPT_REUSED",
                            subject + ".z0_final_evidence.suite_receipts." + lane_id,
                            "each lane requires a distinct receipt path and identity",
                        )
                    )
                    continue
                seen_paths.add(path)
                seen_receipt_ids.add(item["receipt_id"])
                errors.extend(
                    _validate_z0_suite_receipt(
                        suite_receipt,
                        lane_id=lane_id,
                        manifest=manifest,
                        ladder=ladder,
                        subject=subject
                        + ".z0_final_evidence.suite_receipts."
                        + lane_id,
                    )
                )

    promotion_path, promotion_receipt, promotion_errors = _load_z0_bound_receipt(
        manifest.get("promotion_receipt"),
        subject=subject + ".z0_final_evidence.promotion_receipt",
        allowed_root=DEFAULT_RECEIPTS_ROOT,
    )
    errors.extend(promotion_errors)
    if promotion_path is not None and promotion_receipt is not None:
        if (
            set(promotion_receipt.get("packet_ids", [])) != PROMOTION_GATE_PACKETS
            or promotion_receipt.get("release_candidate_id")
            != manifest.get("release_candidate_id")
            or promotion_receipt.get("deployment_generation_id")
            != manifest.get("deployment_generation_id")
            or promotion_receipt.get("realm_config_fingerprint")
            != manifest.get("production_realm_config_fingerprint")
        ):
            errors.append(
                error(
                    "Z0_PROMOTION_BINDING_DRIFT",
                    subject + ".z0_final_evidence.promotion_receipt",
                    "promotion packets, release, generation, or production realm differ",
                )
            )
        else:
            errors.extend(
                _validate_realm_promotion_evidence(
                    promotion_receipt,
                    subject + ".z0_final_evidence.promotion_receipt",
                )
            )
    return errors


def validate_execution_receipts(
    receipts: list[dict[str, Any]],
    catalog: dict[str, Any],
    ladder: dict[str, Any],
    matrix: dict[str, Any],
    checkpoints: Sequence[dict[str, Any]] = (),
    sbe_report: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    schema = _load(DEFAULT_RECEIPT_SCHEMA, "receipt_schema", errors)
    if not isinstance(schema, dict):
        errors.append(error("RECEIPT_SCHEMA_INVALID", "receipt_schema", "object schema required"))
        return [], errors
    rung_by_lane: dict[str, dict[str, Any]] = {}
    for rung in ladder.get("rungs", []):
        if not isinstance(rung, dict):
            continue
        for lane in rung.get("lanes", []):
            if isinstance(lane, dict) and isinstance(lane.get("id"), str):
                rung_by_lane[lane["id"]] = rung
    packet_ids = {item.get("id") for item in catalog.get("packets", []) if isinstance(item, dict)}
    packets_by_id = {
        item.get("id"): item
        for item in catalog.get("packets", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    matrix_ids = {item.get("id") for item in matrix.get("rows", []) if isinstance(item, dict)}
    forbidden = {str(item).lower() for item in ladder.get("receipt_contract", {}).get("forbidden_content", [])}
    current_bindings = {
        "source_manifest_sha256": sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": sha256_file(KNOWLEDGE_DOCS_ROOT / "second_brain_evolution" / "FORWARD_SCHEMA.md"),
        "agents_md_sha256": sha256_file(PROJECT_ROOT / "AGENTS.md"),
    }
    checkpoints_by_packet = {
        item.get("packet_id"): item
        for item in checkpoints
        if isinstance(item, dict) and isinstance(item.get("packet_id"), str)
    }
    valid: list[dict[str, Any]] = []
    for index, receipt_with_path in enumerate(receipts):
        subject = f"receipts[{index}]"
        if not isinstance(receipt_with_path, dict):
            errors.append(error("RECEIPT_DOCUMENT_SHAPE", subject, "receipt must be an object"))
            continue
        receipt = {key: value for key, value in receipt_with_path.items() if key != "__path__"}
        local_errors: list[dict[str, str]] = []
        for item in JsonSchemaValidator(schema).validate(receipt):
            local_errors.append(error(f"RECEIPT_{item['code']}", subject + item["subject"][1:], item["message"]))
        if local_errors:
            errors.extend(local_errors)
            continue
        for key in receipt:
            if key.lower() in forbidden:
                local_errors.append(error("RECEIPT_FORBIDDEN_CONTENT", subject + "." + key, key))
        lane_id = receipt.get("lane_id")
        rung = rung_by_lane.get(lane_id)
        if rung is None or receipt.get("rung_id") != rung.get("id"):
            local_errors.append(error("RECEIPT_LANE_RUNG_MISMATCH", subject, str(lane_id)))
        else:
            rung_packets = set(rung.get("packets", []))
            receipt_packets = set(receipt.get("packet_ids", []))
            invalid_lane_packets = {
                packet_id
                for packet_id in receipt_packets
                if packet_id not in rung_packets
                and lane_id not in packets_by_id.get(packet_id, {}).get("validation_ids", [])
            }
            if invalid_lane_packets:
                local_errors.append(error("RECEIPT_PACKET_RUNG_MISMATCH", subject + ".packet_ids", "receipt packets escape the rung"))
        if not set(receipt.get("packet_ids", [])).issubset(packet_ids):
            local_errors.append(error("RECEIPT_PACKET_UNKNOWN", subject + ".packet_ids", "unknown packet id"))
        if not set(receipt.get("matrix_row_ids", [])).issubset(matrix_ids):
            local_errors.append(error("RECEIPT_MATRIX_ROW_UNKNOWN", subject + ".matrix_row_ids", "unknown matrix row"))
        expected_bindings = dict(current_bindings)
        bound_checkpoints = {
            (packet_id, id(checkpoints_by_packet[packet_id])): checkpoints_by_packet[packet_id]
            for packet_id in receipt.get("packet_ids", [])
            if packet_id in checkpoints_by_packet
        }
        historical_binding_sets: list[dict[str, str]] = []
        for (packet_id, _checkpoint_identity), checkpoint in bound_checkpoints.items():
            historical_bindings = dict(current_bindings)
            # Z0 evidence is sealed before the status integrator changes the two
            # hot manifests.  Its checkpoint pre-hashes break that otherwise
            # circular completion dependency.  Earlier packets remain bound to
            # their historical post-integration hashes.
            integration_hashes = checkpoint.get(
                "pre_edit_sha256" if packet_id == "Z0-P1" else "post_edit_sha256",
                {},
            )
            historical_paths = {
                "source_manifest_sha256": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_packets.v2.json",
                "validation_manifest_sha256": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_validation_ladder.v1.json",
                "schema_catalog_sha256": "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_completion_matrix.v1.json",
            }
            for key, path in historical_paths.items():
                historical = normalize_sha256(integration_hashes.get(path))
                if historical is not None:
                    historical_bindings[key] = historical
            historical_lineage = normalize_sha256(checkpoint.get("current_schema_lineage_sha256"))
            historical_agents = normalize_sha256(checkpoint.get("agents_md_sha256"))
            if historical_lineage is not None:
                historical_bindings["current_schema_lineage_sha256"] = historical_lineage
            if historical_agents is not None:
                historical_bindings["agents_md_sha256"] = historical_agents
            historical_binding_sets.append(historical_bindings)
        if historical_binding_sets:
            first_historical = historical_binding_sets[0]
            if any(item != first_historical for item in historical_binding_sets[1:]):
                local_errors.append(
                    error(
                        "RECEIPT_CHECKPOINT_BINDING_AMBIGUOUS",
                        subject + ".packet_ids",
                        "receipt packets resolve to checkpoints with different authority bindings",
                    )
                )
            else:
                expected_bindings = first_historical
        for key, expected in expected_bindings.items():
            if normalize_sha256(receipt.get(key)) != expected:
                local_errors.append(error("RECEIPT_BINDING_DRIFT", subject + "." + key, "receipt is stale"))
        if receipt.get("mode") == "gate":
            if normalize_sha256(receipt.get("release_candidate_id")) is None or normalize_sha256(receipt.get("realm_config_fingerprint")) is None:
                local_errors.append(error("RECEIPT_GATE_BINDING_INVALID", subject, "gate receipt requires canonical release and realm hashes"))
            if receipt.get("result") != "passed" or receipt.get("exit_code") != 0:
                local_errors.append(error("RECEIPT_GATE_RESULT_INVALID", subject, "gate receipt did not pass"))
            if receipt.get("skipped") != 0 or receipt.get("xfailed") != 0:
                local_errors.append(error("RECEIPT_SKIP_XFAIL_FORBIDDEN", subject, "closing receipt must have zero skips and xfails"))
            if receipt.get("source_pre_sha256") != receipt.get("source_post_sha256"):
                local_errors.append(error("RECEIPT_SOURCE_DRIFT", subject, "validation changed its bound sources"))
            if not local_errors and V0_FINAL_EVIDENCE_PACKETS.intersection(receipt["packet_ids"]):
                local_errors.extend(_validate_v0_final_evidence(receipt, subject))
            if not local_errors and TARGET_GATE_PACKETS.intersection(receipt["packet_ids"]):
                local_errors.extend(_validate_target_gate_evidence(receipt, subject))
            if not local_errors and PROMOTION_GATE_PACKETS.intersection(
                receipt["packet_ids"]
            ):
                local_errors.extend(
                    _validate_realm_promotion_evidence(receipt, subject)
                )
            if not local_errors and "Z0-P1" in receipt["packet_ids"]:
                local_errors.extend(
                    _validate_z0_final_evidence(
                        receipt,
                        subject,
                        ladder=ladder,
                        sbe_report=sbe_report,
                    )
                )
        if local_errors:
            errors.extend(local_errors)
        elif receipt.get("mode") == "gate" and receipt.get("result") == "passed":
            valid.append(receipt)
    return valid, errors


def _packet_receipt_state(
    catalog: dict[str, Any],
    checkpoints: Sequence[dict[str, Any]],
    p1: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> tuple[dict[str, bool], dict[str, list[dict[str, Any]]]]:
    states: dict[str, bool] = {}
    by_packet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for packet in catalog.get("packets", []):
        if not isinstance(packet, dict) or not isinstance(packet.get("id"), str):
            continue
        states[packet["id"]] = False
    for receipt in receipts:
        for packet_id in receipt.get("packet_ids", []):
            if packet_id in states:
                states[packet_id] = True
                by_packet[packet_id].append(receipt)
    states["U0A-P1"] = bool(p1.get("packet_receipt", {}).get("gate_passed"))
    for checkpoint in checkpoints:
        packet_id = checkpoint.get("packet_id")
        if packet_id in RECEIPT_ONLY_PACKETS:
            # A checkpoint alone cannot bypass a dedicated evidence verifier.
            continue
        if packet_id in states:
            states[packet_id] = bool(
                checkpoint.get("packet_status") == "gate_passed"
                and checkpoint.get("validation_receipt", {}).get("status") == "pass"
            )
    return states, by_packet


def completion_state(
    catalog: dict[str, Any],
    ladder: dict[str, Any],
    matrix: dict[str, Any],
    checkpoints: Sequence[dict[str, Any]],
    p1: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    receipt_state, receipts_by_packet = _packet_receipt_state(catalog, checkpoints, p1, receipts)
    packets = [item for item in catalog.get("packets", []) if isinstance(item, dict)]
    gate_passed_ids = {item["id"] for item in packets if item.get("status") == "gate_passed"}
    for packet_id in sorted(gate_passed_ids):
        if not receipt_state.get(packet_id):
            errors.append(error("PACKET_FALSE_GREEN", f"packet:{packet_id}", "gate_passed lacks a bound passing receipt"))
    required_packets = {item["id"] for item in packets if item.get("required") is True}
    passed_required = required_packets & gate_passed_ids
    registry = catalog.get("matrix_closure_registry", {})
    matrix_ids = {item.get("id") for item in matrix.get("rows", []) if isinstance(item, dict)}
    closed_rows = {
        row_id for row_id, owner in registry.items()
        if owner in gate_passed_ids
        and receipt_state.get(owner)
        and any(row_id in receipt.get("matrix_row_ids", []) for receipt in receipts_by_packet.get(owner, []))
    }
    phase_gate_ids = {gate for item in packets for gate in item.get("gate_ids", []) if item.get("status") == "gate_passed" and receipt_state.get(item.get("id"))}
    all_phase_gates = {item.get("id") for item in catalog.get("phase_gates", []) if isinstance(item, dict)}
    open_decisions = list(matrix.get("open_decisions", []))
    pending_lanes = [
        lane.get("id") for rung in ladder.get("rungs", []) if isinstance(rung, dict)
        for lane in rung.get("lanes", []) if isinstance(lane, dict) and lane.get("state") == "planned_required"
    ]
    always_tokens = {"always", "always_for_product_completion"}
    required_approvals = {
        item.get("approval", {}).get("id")
        for item in packets
        if isinstance(item.get("approval"), dict)
        and always_tokens.intersection(item["approval"].get("required_when", []))
    }
    approval_status = {
        item.get("id"): item.get("status")
        for item in catalog.get("approval_registry", []) if isinstance(item, dict)
    }
    satisfied_approval_states = {"approved", "consumed", "satisfied", "waived_not_required"}
    satisfied_approvals = {item for item in required_approvals if approval_status.get(item) in satisfied_approval_states}
    required_external_gates = {
        external_id
        for item in packets if item.get("required") is True
        for external_id in item.get("external_prerequisites", [])
    }
    external_status = {
        item.get("id"): item.get("status")
        for item in catalog.get("external_gate_registry", []) if isinstance(item, dict)
    }
    satisfied_external_states = {"satisfied", "gate_passed", "handoff_accepted"}
    satisfied_external = {item for item in required_external_gates if external_status.get(item) in satisfied_external_states}
    completion = (
        required_packets == passed_required
        and matrix_ids == closed_rows
        and all_phase_gates == phase_gate_ids
        and not open_decisions
        and not pending_lanes
        and required_approvals == satisfied_approvals
        and required_external_gates == satisfied_external
    )
    if catalog.get("claims", {}).get("product_complete") and not completion:
        errors.append(error("PRODUCT_FALSE_GREEN", "catalog.claims.product_complete", "completion prerequisites are not closed"))
    if ladder.get("claims", {}).get("product_complete") and not completion:
        errors.append(error("PRODUCT_FALSE_GREEN", "ladder.claims.product_complete", "completion prerequisites are not closed"))
    if matrix.get("claims", {}).get("internal_product_complete") and not completion:
        errors.append(error("PRODUCT_FALSE_GREEN", "matrix.claims.internal_product_complete", "completion prerequisites are not closed"))
    summary = {
        "required_packets": len(required_packets),
        "gate_passed_required_packets": len(passed_required),
        "matrix_rows": len(matrix_ids),
        "closed_matrix_rows": len(closed_rows),
        "phase_gates": len(all_phase_gates),
        "closed_phase_gates": len(phase_gate_ids),
        "open_decisions": len(open_decisions),
        "planned_required_lanes": len(pending_lanes),
        "required_approvals": len(required_approvals),
        "satisfied_approvals": len(satisfied_approvals),
        "required_external_gates": len(required_external_gates),
        "satisfied_external_gates": len(satisfied_external),
        "valid_execution_receipts": len(receipts),
        "product_complete": completion,
    }
    return summary, errors


def verify_bundle(
    *,
    mode: str = "audit",
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
    matrix_path: Path = DEFAULT_MATRIX,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    u0a_checkpoint_path: Path = DEFAULT_U0A_CHECKPOINT,
    p1_checkpoint_path: Path = DEFAULT_P1_CHECKPOINT,
    receipts_root: Path = DEFAULT_RECEIPTS_ROOT,
    sbe_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    if mode not in {"audit", "gate"}:
        raise ValueError("mode must be audit or gate")
    errors: list[dict[str, str]] = []
    catalog = _load(Path(catalog_path), "catalog", errors)
    ladder = _load(Path(ladder_path), "ladder", errors)
    matrix = _load(Path(matrix_path), "matrix", errors)
    checkpoint_pointer: dict[str, Any] | None = None
    resolved_checkpoint_path = Path(checkpoint_path)
    try:
        checkpoint, resolved_checkpoint_path, checkpoint_pointer = load_current_checkpoint(Path(checkpoint_path))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(error("CHECKPOINT_READ_FAILED", "checkpoint", str(exc)))
        checkpoint = None
    u0a_checkpoint = _load(Path(u0a_checkpoint_path), "u0a_checkpoint", errors)
    p1 = _load(Path(p1_checkpoint_path), "p1_checkpoint", errors)
    if sbe_report is None:
        sbe_report, sbe_exit = audit_contracts(KNOWLEDGE_DOCS_ROOT.resolve())
    else:
        sbe_exit = 0 if sbe_report.get("status") == "pass" and sbe_report.get("error_count") == 0 else 1
    if sbe_exit != 0:
        errors.append(error("SBE_CONTRACT_AUDIT_FAILED", "knowledge_spaces", f"error_count={sbe_report.get('error_count')}"))
    if not all(isinstance(item, dict) for item in (catalog, ladder, matrix, checkpoint, u0a_checkpoint, p1)):
        final_errors = sorted_errors(errors)
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "contract": "code_assist.notion.internal_collaborative_validation.v1",
            "mode": mode,
            "status": "fail",
            "completion_state": "invalid",
            "error_count": len(final_errors),
            "errors": final_errors,
            "summary": {},
        }
        return report, 1

    errors.extend(validate_catalog(catalog, ladder, matrix, sbe_report))
    errors.extend(validate_ladder(ladder, catalog, matrix, sbe_report))
    errors.extend(validate_checkpoint(checkpoint, catalog, ladder, sbe_report, catalog_path=Path(catalog_path), ladder_path=Path(ladder_path)))
    checkpoints = load_checkpoint_chain(
        checkpoint,
        current_path=resolved_checkpoint_path,
        u0a_checkpoint=u0a_checkpoint,
        u0a_checkpoint_path=Path(u0a_checkpoint_path),
        errors=errors,
    )
    loaded_receipts = load_execution_receipts(Path(receipts_root), errors)
    valid_receipts, receipt_errors = validate_execution_receipts(
        loaded_receipts,
        catalog,
        ladder,
        matrix,
        checkpoints,
        sbe_report=sbe_report,
    )
    errors.extend(receipt_errors)
    summary, completion_errors = completion_state(catalog, ladder, matrix, checkpoints, p1, valid_receipts)
    errors.extend(completion_errors)
    structural_errors = sorted_errors(errors)
    if mode == "gate" and not summary.get("product_complete"):
        errors.append(error("STRICT_COMPLETION_INCOMPLETE", "<gate>", "all required packets, rows, gates, decisions, and lanes must close"))
    final_errors = sorted_errors(errors)
    status = "pass" if not final_errors else "fail"
    completion_label = "complete" if summary.get("product_complete") else "incomplete"
    if structural_errors:
        completion_label = "invalid"
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_validation.v1",
        "mode": mode,
        "status": status,
        "completion_state": completion_label,
        "error_count": len(final_errors),
        "errors": final_errors,
        "summary": summary,
        "bindings": {
            "catalog_sha256": sha256_file(Path(catalog_path)),
            "ladder_sha256": sha256_file(Path(ladder_path)),
            "matrix_sha256": sha256_file(Path(matrix_path)),
            "checkpoint_sha256": sha256_file(resolved_checkpoint_path),
            "checkpoint_pointer_sha256": sha256_file(Path(checkpoint_path)) if checkpoint_pointer is not None else None,
            "u0a_checkpoint_sha256": sha256_file(Path(u0a_checkpoint_path)),
            "sbe_contract_audit_input_sha256": normalize_sha256(sbe_report.get("input_sha256")),
        },
    }
    return report, 0 if status == "pass" else 1


def find_lane(ladder: dict[str, Any], lane_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for rung in ladder.get("rungs", []):
        if not isinstance(rung, dict):
            continue
        for lane in rung.get("lanes", []):
            if isinstance(lane, dict) and lane.get("id") == lane_id:
                return rung, lane
    return None
