"""Immutable numbered migration definitions and deterministic catalog hashes."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata

from ..errors import KnowledgeCoreMigrationError, KnowledgeCoreSchemaMismatchError


_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,119}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_PREFIXES = (
    "ATTACH",
    "BEGIN",
    "COMMIT",
    "DETACH",
    "END TRANSACTION",
    "PRAGMA",
    "RELEASE",
    "ROLLBACK",
    "SAVEPOINT",
    "VACUUM",
)


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_sql(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise KnowledgeCoreMigrationError(
            "Migration SQL statements must be non-empty UTF-8 text."
        )
    statement = value.strip()
    remainder = statement
    while True:
        remainder = remainder.lstrip()
        if remainder and unicodedata.category(remainder[0]) == "Cf":
            raise KnowledgeCoreMigrationError(
                "Migration SQL cannot begin with an invisible format character."
            )
        if remainder.startswith(";"):
            raise KnowledgeCoreMigrationError(
                "A migration statement cannot begin with an empty statement."
            )
        if remainder.startswith("--"):
            newline = remainder.find("\n")
            if newline < 0:
                raise KnowledgeCoreMigrationError(
                    "A migration cannot contain only a SQL comment."
                )
            remainder = remainder[newline + 1 :]
            continue
        if remainder.startswith("/*"):
            end = remainder.find("*/", 2)
            if end < 0:
                raise KnowledgeCoreMigrationError(
                    "A migration contains an unterminated SQL comment."
                )
            remainder = remainder[end + 2 :]
            continue
        break
    leading = remainder.upper()
    if any(leading.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES):
        raise KnowledgeCoreMigrationError(
            "Migration SQL cannot own transactions or deployment pragmas."
        )
    return statement


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    expected_schema_sha256: str
    postcondition_queries: tuple[str, ...] = ()
    requires_foreign_keys_off: bool = False
    # An explicit forward migration can extend an invariant whose historical
    # SQL names the old physical representation. Never edit the historical
    # query/checksum or introduce a runtime skip. Each pair names the exact
    # active prior query and its complete reviewed successor.
    postcondition_replacements: tuple[tuple[str, str], ...] = ()
    checksum_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise KnowledgeCoreMigrationError(
                "Migration versions must be positive integers."
            )
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(self.name):
            raise KnowledgeCoreMigrationError(
                "Migration names must be stable lowercase identifiers."
            )
        statements = tuple(_normalized_sql(value) for value in self.statements)
        if not statements:
            raise KnowledgeCoreMigrationError(
                "A migration must contain at least one SQL statement."
            )
        if (
            not isinstance(self.expected_schema_sha256, str)
            or not _SHA256_PATTERN.fullmatch(self.expected_schema_sha256)
        ):
            raise KnowledgeCoreMigrationError(
                "A migration requires a reviewed physical schema SHA256."
            )
        postconditions = tuple(
            _normalized_sql(value) for value in self.postcondition_queries
        )
        if not isinstance(self.requires_foreign_keys_off, bool):
            raise KnowledgeCoreMigrationError(
                "requires_foreign_keys_off must be a Boolean."
            )
        for query in postconditions:
            leading = query.lstrip(" \t\r\n(").upper()
            if not (leading.startswith("SELECT") or leading.startswith("WITH")):
                raise KnowledgeCoreMigrationError(
                    "Migration postconditions must be read-only SELECT queries."
                )
        if not isinstance(self.postcondition_replacements, tuple):
            raise KnowledgeCoreMigrationError(
                "Postcondition replacements must be immutable query pairs."
            )
        replacements: list[tuple[str, str]] = []
        replaced_queries: set[str] = set()
        for pair in self.postcondition_replacements:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise KnowledgeCoreMigrationError(
                    "Postcondition replacements must be immutable query pairs."
                )
            old_query, new_query = (_normalized_sql(query) for query in pair)
            for query in (old_query, new_query):
                leading = query.lstrip(" \t\r\n(").upper()
                if not (leading.startswith("SELECT") or leading.startswith("WITH")):
                    raise KnowledgeCoreMigrationError(
                        "Postcondition replacements must be read-only SELECT queries."
                    )
            if old_query == new_query or old_query in replaced_queries:
                raise KnowledgeCoreMigrationError(
                    "A postcondition replacement must extend one unique query."
                )
            replaced_queries.add(old_query)
            replacements.append((old_query, new_query))
        object.__setattr__(self, "statements", statements)
        object.__setattr__(self, "postcondition_queries", postconditions)
        object.__setattr__(self, "postcondition_replacements", tuple(replacements))
        checksum_value: dict[str, object] = {
            "format": "code_assist.knowledge_migration.v1",
            "expected_schema_sha256": self.expected_schema_sha256,
            "name": self.name,
            "postcondition_queries": list(postconditions),
            "statements": list(statements),
            "version": self.version,
        }
        # Preserve the frozen v1-v8 checksums byte-for-byte.  The opt-in field
        # is part of a migration's identity only when the reviewed migration
        # actually needs a parent-table rebuild.
        if self.requires_foreign_keys_off:
            checksum_value["requires_foreign_keys_off"] = True
        # Empty opt-ins do not alter any historical numbered identity. The
        # complete old/new SQL belongs to the successor migration's checksum.
        if replacements:
            checksum_value["postcondition_replacements"] = replacements
        object.__setattr__(
            self,
            "checksum_sha256",
            _sha256_json(checksum_value),
        )


@dataclass(frozen=True, slots=True)
class _ResolvedPostcondition:
    origin_version: int
    origin_index: int
    defined_version: int
    query: str


@dataclass(frozen=True, slots=True)
class MigrationCatalog:
    migrations: tuple[Migration, ...]
    checksum_sha256: str = field(init=False)
    resolved_postconditions: tuple[_ResolvedPostcondition, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        migrations = tuple(self.migrations)
        if any(not isinstance(migration, Migration) for migration in migrations):
            raise KnowledgeCoreMigrationError(
                "Migration catalogs may contain only immutable Migration values."
            )
        expected = list(range(1, len(migrations) + 1))
        actual = [migration.version for migration in migrations]
        if actual != expected:
            raise KnowledgeCoreMigrationError(
                "Migration catalogs must be ordered and contiguous from version 1."
            )
        resolved: list[_ResolvedPostcondition] = []
        for migration in migrations:
            # Resolve all targets against the preceding version, not partially
            # applied replacements in this version. Self/future/ambiguous or
            # already retired targets are never silently accepted.
            prior = tuple(resolved)
            for old_query, new_query in migration.postcondition_replacements:
                matches = [index for index, entry in enumerate(prior)
                           if entry.query == old_query]
                if len(matches) != 1:
                    raise KnowledgeCoreMigrationError(
                        "A replacement must name exactly one active prior postcondition."
                    )
                index = matches[0]
                old = prior[index]
                resolved[index] = _ResolvedPostcondition(
                    old.origin_version, old.origin_index, migration.version, new_query
                )
            resolved.extend(
                _ResolvedPostcondition(migration.version, index, migration.version, query)
                for index, query in enumerate(migration.postcondition_queries, 1)
            )
        object.__setattr__(self, "migrations", migrations)
        object.__setattr__(self, "resolved_postconditions", tuple(resolved))
        object.__setattr__(
            self,
            "checksum_sha256",
            _sha256_json(
                {
                    "format": "code_assist.knowledge_migration_catalog.v1",
                    "migrations": [
                        {
                            "checksum_sha256": migration.checksum_sha256,
                            "name": migration.name,
                            "version": migration.version,
                        }
                        for migration in migrations
                    ],
                }
            ),
        )

    @property
    def latest_version(self) -> int:
        return len(self.migrations)

    def migration(self, version: int) -> Migration:
        if version < 1 or version > self.latest_version:
            raise KnowledgeCoreSchemaMismatchError(
                "The database references a migration unknown to this build."
            )
        return self.migrations[version - 1]


__all__ = ["Migration", "MigrationCatalog"]
