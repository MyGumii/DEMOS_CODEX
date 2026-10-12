"""Forward-only retained provenance for authorized protected-source extraction.

The original blob stays in ``source_artifact``.  A clear derivative is never
stored here; this immutable row records its digest, the approved opening route,
and the exact location coverage reported by the extractor.  No v1-v49 schema
or previously issued source/preview receipt is changed.
"""


_UUID = """length({x})=36 AND substr({x},9,1)='-' AND substr({x},14,1)='-'
  AND substr({x},19,1)='-' AND substr({x},24,1)='-'
  AND length(replace({x},'-',''))=32
  AND replace({x},'-','') NOT GLOB '*[^0-9a-f]*' AND {x}=lower({x})"""
_SHA = """length({x})=64 AND {x}=lower({x})
  AND {x} NOT GLOB '*[^0-9a-f]*'"""


V050_STATEMENTS = (
    f"""CREATE TABLE protected_extraction_provenance(
      extraction_snapshot_id TEXT PRIMARY KEY NOT NULL
        CHECK({_UUID.format(x='extraction_snapshot_id')})
        REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
      artifact_id TEXT NOT NULL CHECK({_UUID.format(x='artifact_id')})
        REFERENCES source_artifact(id) ON DELETE RESTRICT,
      original_sha256 TEXT NOT NULL CHECK({_SHA.format(x='original_sha256')}),
      derivative_sha256 TEXT CHECK(derivative_sha256 IS NULL OR
        ({_SHA.format(x='derivative_sha256')})),
      source_kind TEXT NOT NULL CHECK(source_kind IN
        ('pdf_standard_encryption','office_encrypted_package_candidate')),
      opening_method TEXT NOT NULL CHECK(length(trim(opening_method)) BETWEEN 1 AND 120),
      extraction_method TEXT NOT NULL CHECK(length(trim(extraction_method)) BETWEEN 1 AND 120),
      location_kind TEXT NOT NULL CHECK(location_kind IN
        ('page','derived_page','slide','sheet')),
      expected_locations INTEGER NOT NULL CHECK(expected_locations BETWEEN 1 AND 500),
      coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)
        AND json_type(coverage_json)='array'
        AND json_array_length(coverage_json) BETWEEN 1 AND 500
        AND length(CAST(coverage_json AS BLOB))<=131072),
      visual_coverage TEXT NOT NULL CHECK(visual_coverage IN
        ('not_proven','provider_reported')),
      created_at INTEGER NOT NULL CHECK(created_at>=0),
      UNIQUE(artifact_id,extraction_snapshot_id)
    ) STRICT""",
    """CREATE INDEX ix_protected_extraction_provenance_artifact
       ON protected_extraction_provenance(artifact_id)""",
    """CREATE TRIGGER trg_protected_extraction_provenance_insert
       BEFORE INSERT ON protected_extraction_provenance
       WHEN NOT EXISTS(
         SELECT 1 FROM extraction_snapshot e
         JOIN source_artifact a ON a.id=e.artifact_id
         WHERE e.id=NEW.extraction_snapshot_id
           AND e.artifact_id=NEW.artifact_id
           AND e.status='ready'
           AND a.raw_sha256=NEW.original_sha256
           AND a.origin_type='upload'
           AND e.extractor_name IN
             ('canonical-approved-owner-pdf','canonical-authorized-protected-snapshot')
       ) OR json_array_length(NEW.coverage_json)<>NEW.expected_locations
         OR EXISTS(
           SELECT 1 FROM json_each(NEW.coverage_json) location
           WHERE location.type<>'object'
             OR coalesce(json_type(location.value,'$.ordinal')='integer',0)=0
             OR coalesce(json_extract(location.value,'$.ordinal')
                 =CAST(location.key AS INTEGER)+1,0)=0
             OR coalesce(json_extract(location.value,'$.status') IN
               ('complete','confirmed_blank'),0)=0
             OR coalesce(json_type(location.value,'$.visual_present')
                 IN ('true','false'),0)=0
             OR coalesce(json_type(location.value,'$.visual_examined')
                 IN ('true','false'),0)=0
             OR (json_extract(location.value,'$.visual_present')=1
                 AND json_extract(location.value,'$.visual_examined')=0
                 AND NEW.visual_coverage='provider_reported')
         )
       BEGIN SELECT RAISE(ABORT,'PROTECTED_SOURCE_PROVENANCE_INVALID'); END""",
    """CREATE TRIGGER trg_protected_extraction_provenance_update
       BEFORE UPDATE ON protected_extraction_provenance
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER trg_protected_extraction_provenance_delete
       BEFORE DELETE ON protected_extraction_provenance
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
)

V050_POSTCONDITION_QUERIES = (
    """SELECT 'protected_source_provenance_orphan'
       FROM protected_extraction_provenance p
       LEFT JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
       LEFT JOIN source_artifact a ON a.id=p.artifact_id
       WHERE e.id IS NULL OR a.id IS NULL OR e.artifact_id<>p.artifact_id
         OR a.raw_sha256<>p.original_sha256 OR e.status<>'ready'
       LIMIT 1""",
)
