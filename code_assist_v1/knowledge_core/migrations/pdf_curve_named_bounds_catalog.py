"""Explicit V84 named curve-bounds successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_bounded_text_partition_catalog import PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG
from .v084_pdf_curve_named_bounds import V084_STATEMENTS, V084_POSTCONDITION_REPLACEMENTS

V084_EXPECTED_SCHEMA_SHA256 = 'f3f13aed85e27fb11bb12534c2466677968fc83dd7f1a041af4eaae94c0bca86'
SCHEMA_V84_MIGRATION = Migration(version=84, name='pdf_curve_named_bounds_v84',
    statements=V084_STATEMENTS, expected_schema_sha256=V084_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V084_POSTCONDITION_REPLACEMENTS)
PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations, SCHEMA_V84_MIGRATION))
