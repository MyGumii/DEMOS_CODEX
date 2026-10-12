"""Executable canonical migration catalog through physical schema version 13."""

from __future__ import annotations

from .model import Migration, MigrationCatalog
from .v001_catalog import SCHEMA_V1_MIGRATION
from .v002_account_auth_policy import (
    V002_POSTCONDITION_QUERIES,
    V002_STATEMENTS,
)
from .v003_legacy_migration_map_durability import (
    V003_POSTCONDITION_QUERIES,
    V003_STATEMENTS,
)
from .v004_legacy_migration_relationship_map import (
    V004_POSTCONDITION_QUERIES,
    V004_STATEMENTS,
)
from .v005_legacy_p24_effect_map import (
    V005_POSTCONDITION_QUERIES,
    V005_STATEMENTS,
)
from .v006_legacy_p25_effect_map import (
    V006_POSTCONDITION_QUERIES,
    V006_STATEMENTS,
)
from .v007_blob_gc_claim_safety import (
    V007_POSTCONDITION_QUERIES,
    V007_STATEMENTS,
)
from .v008_personal_wiki_profiles import (
    V008_POSTCONDITION_QUERIES,
    V008_STATEMENTS,
)
from .v009_higher_native_spaces import (
    V009_POSTCONDITION_QUERIES,
    V009_STATEMENTS,
)
from .v010_semantic_graph_contracts import (
    V010_POSTCONDITION_QUERIES,
    V010_STATEMENTS,
)
from .v011_higher_publication_authority import (
    V011_POSTCONDITION_QUERIES,
    V011_STATEMENTS,
)
from .v012_temporal_projection_footer import (
    V012_POSTCONDITION_QUERIES,
    V012_STATEMENTS,
)
from .v013_direct_org_affiliation import (
    V013_POSTCONDITION_QUERIES,
    V013_STATEMENTS,
)


# Generated from cumulative V001_STATEMENTS + V002_STATEMENTS by
# build_cumulative_schema_sha256().  This value is replaced only together with
# an independently reproduced migration test and evidence record.
V002_EXPECTED_SCHEMA_SHA256 = (
    "d8c6a2d7e17893740280264f1c246c2bdabe97b0a14fd2fa0be4ccf726a777bf"
)

SCHEMA_V2_MIGRATION = Migration(
    version=2,
    name="account_auth_policy_v2",
    statements=V002_STATEMENTS,
    expected_schema_sha256=V002_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V002_POSTCONDITION_QUERIES,
)

# Generated from cumulative V001_STATEMENTS + V002_STATEMENTS +
# V003_STATEMENTS by build_cumulative_schema_sha256() using the official
# project venv with require_approved_runtime=False.  Versions 1 and 2 remain
# frozen; this reviewed value identifies the forward-only version-3 schema.
V003_EXPECTED_SCHEMA_SHA256 = (
    "9c7563cfff4ec6e17cef635b140df663c6924f5a285d6ce072e4a4b9e556057a"
)

SCHEMA_V3_MIGRATION = Migration(
    version=3,
    name="legacy_migration_map_durability_v3",
    statements=V003_STATEMENTS,
    expected_schema_sha256=V003_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V003_POSTCONDITION_QUERIES,
)

# Generated from cumulative V001_STATEMENTS + V002_STATEMENTS +
# V003_STATEMENTS + V004_STATEMENTS by build_cumulative_schema_sha256() using
# the official project venv with require_approved_runtime=False.  Versions 1
# through 3 remain frozen; this reviewed value identifies the forward-only
# version-4 schema.
V004_EXPECTED_SCHEMA_SHA256 = (
    "589e1cb328e98bb3d13c86dc117c10e0eee0fb13d2542d5923c2348e86f10fdd"
)

SCHEMA_V4_MIGRATION = Migration(
    version=4,
    name="legacy_migration_relationship_map_v4",
    statements=V004_STATEMENTS,
    expected_schema_sha256=V004_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V004_POSTCONDITION_QUERIES,
)

V005_EXPECTED_SCHEMA_SHA256 = (
    "05831cad4338a49c16905cfc5daabd7e85973dbf33ed833706c4f172b0a6eb96"
)

SCHEMA_V5_MIGRATION = Migration(
    version=5,
    name="legacy_p24_effect_map_v5",
    statements=V005_STATEMENTS,
    expected_schema_sha256=V005_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V005_POSTCONDITION_QUERIES,
)

V006_EXPECTED_SCHEMA_SHA256 = (
    "32d38a5942119766bc96f28b51a302b4c005f484bf37bc23f2f23cfdf5b138c2"
)

SCHEMA_V6_MIGRATION = Migration(
    version=6,
    name="legacy_p25_effect_map_v6",
    statements=V006_STATEMENTS,
    expected_schema_sha256=V006_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V006_POSTCONDITION_QUERIES,
)

V007_EXPECTED_SCHEMA_SHA256 = (
    "5ca2cb78e1433ac8ce6976263d578df6f3a625b73bce5f8556b93d395db585a4"
)

SCHEMA_V7_MIGRATION = Migration(
    version=7,
    name="blob_gc_claim_safety_v7",
    statements=V007_STATEMENTS,
    expected_schema_sha256=V007_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V007_POSTCONDITION_QUERIES,
)

V008_EXPECTED_SCHEMA_SHA256 = (
    "db47b16c5ffdbfb682ee7459682cbff2876d80d5dc95e6e46617c6deef9ba55a"
)

SCHEMA_V8_MIGRATION = Migration(
    version=8,
    name="personal_wiki_profiles_v8",
    statements=V008_STATEMENTS,
    expected_schema_sha256=V008_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V008_POSTCONDITION_QUERIES,
)

V009_EXPECTED_SCHEMA_SHA256 = (
    "f6659839db6be4f167a0946e863e8f27ff9d46c6e1bc74bae73d30dbffd658b2"
)

SCHEMA_V9_MIGRATION = Migration(
    version=9,
    name="higher_native_spaces_v9",
    statements=V009_STATEMENTS,
    expected_schema_sha256=V009_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V009_POSTCONDITION_QUERIES,
    requires_foreign_keys_off=True,
)

V010_EXPECTED_SCHEMA_SHA256 = (
    "db8c6abd293b30892b7b8b09c1de31ebc4c9036b532bbecb78064df901e570b5"
)

SCHEMA_V10_MIGRATION = Migration(
    version=10,
    name="semantic_graph_contracts_v10",
    statements=V010_STATEMENTS,
    expected_schema_sha256=V010_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V010_POSTCONDITION_QUERIES,
    requires_foreign_keys_off=True,
)

V011_EXPECTED_SCHEMA_SHA256 = (
    "1638cc6090a60fe1680b67fe6a6394d264b2cd596156bc4062b88948ec75b90a"
)

SCHEMA_V11_MIGRATION = Migration(
    version=11,
    name="higher_publication_authority_v11",
    statements=V011_STATEMENTS,
    expected_schema_sha256=V011_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V011_POSTCONDITION_QUERIES,
    requires_foreign_keys_off=True,
)

V012_EXPECTED_SCHEMA_SHA256 = (
    "b9dc73caa11ff955be43964d1322e115847d9f07b2cb7c54b4fc76edf2f1ff20"
)

SCHEMA_V12_MIGRATION = Migration(
    version=12,
    name="temporal_provenance_v12",
    statements=V012_STATEMENTS,
    expected_schema_sha256=V012_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V012_POSTCONDITION_QUERIES,
)

V013_EXPECTED_SCHEMA_SHA256 = (
    "2326851c0be29ba6ee5f44ae9886cc9cf4c24d2b9a4f369e52574515fd69a2b9"
)

SCHEMA_V13_MIGRATION = Migration(
    version=13,
    name="direct_org_affiliation_v13",
    statements=V013_STATEMENTS,
    expected_schema_sha256=V013_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V013_POSTCONDITION_QUERIES,
)

CANONICAL_MIGRATION_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
        SCHEMA_V8_MIGRATION,
        SCHEMA_V9_MIGRATION,
        SCHEMA_V10_MIGRATION,
        SCHEMA_V11_MIGRATION,
        SCHEMA_V12_MIGRATION,
        SCHEMA_V13_MIGRATION,
    )
)


__all__ = [
    "CANONICAL_MIGRATION_CATALOG",
    "SCHEMA_V2_MIGRATION",
    "SCHEMA_V3_MIGRATION",
    "SCHEMA_V4_MIGRATION",
    "SCHEMA_V5_MIGRATION",
    "SCHEMA_V6_MIGRATION",
    "SCHEMA_V7_MIGRATION",
    "SCHEMA_V8_MIGRATION",
    "SCHEMA_V9_MIGRATION",
    "SCHEMA_V10_MIGRATION",
    "SCHEMA_V11_MIGRATION",
    "SCHEMA_V12_MIGRATION",
    "SCHEMA_V13_MIGRATION",
    "V002_EXPECTED_SCHEMA_SHA256",
    "V003_EXPECTED_SCHEMA_SHA256",
    "V004_EXPECTED_SCHEMA_SHA256",
    "V005_EXPECTED_SCHEMA_SHA256",
    "V006_EXPECTED_SCHEMA_SHA256",
    "V007_EXPECTED_SCHEMA_SHA256",
    "V008_EXPECTED_SCHEMA_SHA256",
    "V009_EXPECTED_SCHEMA_SHA256",
    "V010_EXPECTED_SCHEMA_SHA256",
    "V011_EXPECTED_SCHEMA_SHA256",
    "V012_EXPECTED_SCHEMA_SHA256",
    "V013_EXPECTED_SCHEMA_SHA256",
]
