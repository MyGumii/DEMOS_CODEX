"""Forward-only provenance kind for an approved policy-protected source.

No existing protected row is reclassified. The v50 table is rebuilt with one
additional CHECK value, retaining its other checks and immutable triggers.
This does not detect or open vendor DRM by itself.
"""

from .v050_protected_extraction_provenance import V050_STATEMENTS


_OLD_KIND_CHECK = (
    "('pdf_standard_encryption','office_encrypted_package_candidate')"
)
_NEW_KIND_CHECK = (
    "('pdf_standard_encryption','office_encrypted_package_candidate',"
    "'company_policy_protected')"
)
assert V050_STATEMENTS[0].count(_OLD_KIND_CHECK) == 1

V060_STATEMENTS = (
    V050_STATEMENTS[0]
    .replace(
        "CREATE TABLE protected_extraction_provenance(",
        "CREATE TABLE protected_extraction_provenance_v60(",
        1,
    )
    .replace(_OLD_KIND_CHECK, _NEW_KIND_CHECK, 1),
    """INSERT INTO protected_extraction_provenance_v60
       SELECT * FROM protected_extraction_provenance""",
    "DROP TABLE protected_extraction_provenance",
    V050_STATEMENTS[0].replace(_OLD_KIND_CHECK, _NEW_KIND_CHECK, 1),
    """INSERT INTO protected_extraction_provenance
       SELECT * FROM protected_extraction_provenance_v60""",
    "DROP TABLE protected_extraction_provenance_v60",
    *V050_STATEMENTS[1:],
)

V060_POSTCONDITION_QUERIES = ()


__all__ = ["V060_STATEMENTS", "V060_POSTCONDITION_QUERIES"]
