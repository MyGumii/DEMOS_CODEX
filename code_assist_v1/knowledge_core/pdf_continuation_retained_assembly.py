"""V98-only exact hierarchical assembly of repository-replayed outputs.

The normal repository must first replay every source/input/output/transport
receipt under its current authority. This helper neither admits an output nor
provides publication authority. It accepts no validation/materialization
callbacks. Sealed leaf identities and a complete split forest prove ownership
by induction, without lifting the V97 per-split 2--16 child restriction.
"""
from dataclasses import dataclass
import json

from .pdf_visual_continuation import (
    ContinuationLeafRequest, ContinuationPolicy, VERSION, _plan,
    parent_inventory, exact_union, require, project_output, _hash, _json,
    _checked_chart_supplement, _chart_render_options, _literal_supplement,
)
from .pdf_continuation_retained_plan import _leaf, _no_owner_overlap
from .wiki_authoring import AuthoringWorkLimitError


@dataclass(frozen=True, slots=True)
class RetainedAssemblySplit:
    """One repository-replayed actual or inherited complete split.

    Both the parent and every child are sealed requests re-materialized from
    the same original source. Historical refusal/partition pointer checks
    remain the repository's responsibility, not assertions supplied here.
    """

    parent: ContinuationLeafRequest
    children: tuple[ContinuationLeafRequest, ...]


def assemble_retained(plan, original_plan_leaves, tree_rows, active_results, policy):
    """Assemble a complete original-root forest into the unchanged V97 body.

    ``original_plan_leaves`` is the full sealed original batch sequence from
    the normal one-pass preparation. ``tree_rows`` contains every complete
    split, including intermediate nodes, and no orphans. ``active_results``
    is the existing repository-validated result-dict tuple in its normal order.
    Legacy original ``reuse`` has the original batch request SHA; ``write``
    (including a direct retained write reference) has its sealed leaf SHA.
    Extra tagged provenance fields are retained in the existing output manifest.
    No model, DB, job, new receipt, or decoded-result callback is introduced.
    """
    p = _plan(plan)
    require(type(policy) is ContinuationPolicy, "Use an admitted continuation policy")
    policy.encoded()
    require(type(original_plan_leaves) is tuple and original_plan_leaves
        and type(tree_rows) is tuple and type(active_results) is tuple and active_results,
        "Retained assembly requires complete typed root, split and result tuples")
    inventory = parent_inventory(p)
    require(len(original_plan_leaves) == len(inventory), "Missing original assembly roots")
    parents = {parent["ordinal"]: parent for parent in inventory}
    require(len(parents) == len(inventory), "Repeated original assembly ordinal")
    nodes, coverages, payloads, roots, splits = {}, {}, {}, {}, {}

    def register(leaf):
        payload, coverage = _leaf(leaf, policy)
        digest = leaf.request_sha256
        if digest in nodes:
            require(nodes[digest].identity() == leaf.identity(),
                "One assembly input has conflicting sealed identity")
        else:
            if len(nodes) >= policy.max_leaves:
                raise AuthoringWorkLimitError("Retained assembly exceeds its finite node budget")
            nodes[digest], coverages[digest], payloads[digest] = leaf, coverage, payload
        return digest

    for leaf in original_plan_leaves:
        digest = register(leaf)
        parent = parents.get(leaf.parent_ordinal)
        require(parent is not None and leaf.parent_ordinal not in roots
            and payloads[digest]["exact_parent"] is True
            and payloads[digest]["parent_request_sha256"] == parent["request_sha256"]
            and coverages[digest] == parent["coverage"],
            "Assembly roots differ from the exact original inventory")
        roots[leaf.parent_ordinal] = digest
    _no_owner_overlap(tuple(coverages[digest] for digest in roots.values()))

    for row in tree_rows:
        require(type(row) is RetainedAssemblySplit and type(row.children) is tuple
            and 2 <= len(row.children) <= 16,
            "Each retained split must contain 2--16 sealed children")
        digest = register(row.parent)
        require(digest not in splits, "Repeated assembly split parent")
        children = tuple(register(child) for child in row.children)
        require(len(set(children)) == len(children) and digest not in children,
            "Cyclic or duplicated assembly split child")
        require(all(nodes[child].parent_ordinal == row.parent.parent_ordinal
            and payloads[child]["parent_request_sha256"] == payloads[digest]["parent_request_sha256"]
            and coverages[child] != coverages[digest] for child in children),
            "An assembly child differs from its original parent identity")
        _no_owner_overlap(tuple(coverages[child] for child in children))
        exact_union(coverages[digest], tuple(coverages[child] for child in children))
        splits[digest] = children

    visited, frontier = set(), []

    def visit(digest, depth, ordinal):
        if depth > policy.max_depth:
            raise AuthoringWorkLimitError("Retained assembly exceeds its finite depth budget")
        require(digest not in visited, "Cyclic or multiply owned assembly input")
        visited.add(digest)
        require(nodes[digest].parent_ordinal == ordinal,
            "A retained assembly subtree changed original parent ordinal")
        if digest in splits:
            for child in splits[digest]:
                visit(child, depth + 1, ordinal)
        else:
            frontier.append(digest)

    for ordinal, digest in roots.items():
        visit(digest, 0, ordinal)
    require(visited == set(nodes), "Orphan retained assembly split or child")
    _no_owner_overlap(tuple(coverages[digest] for digest in frontier))
    require(len(active_results) == len(frontier), "Missing or surplus final retained outputs")
    expected = {}
    for digest in frontier:
        leaf = nodes[digest]
        expected[("write", leaf.parent_ordinal, leaf.coverage_json, digest)] = digest
        if roots[leaf.parent_ordinal] == digest:
            expected[("reuse", leaf.parent_ordinal, leaf.coverage_json,
                payloads[digest]["parent_request_sha256"])] = digest
    supplied = set()
    for row in active_results:
        require(type(row) is dict and type(row.get("kind")) is str
            and row["kind"] in ("write", "reuse")
            and type(row.get("parent_ordinal")) is int
            and type(row.get("coverage_json")) is str
            and type(row.get("request_sha256")) is str,
            "Invalid repository-decoded retained result descriptor")
        key = (row["kind"], row["parent_ordinal"], row["coverage_json"], row["request_sha256"])
        digest = expected.get(key)
        require(digest is not None and digest not in supplied,
            "Final output does not match one exact sealed frontier owner")
        supplied.add(digest)
    require(supplied == set(frontier), "Not every retained frontier owner has one final output")

    # Keep the existing V97 assembly/rendering body verbatim. Only ownership
    # validation above is hierarchical; raw replay, tables and receipt formats
    # remain on their normal paths. The pure regression asserts AST equality.
    pieces = []
    for row in active_results:
        require(type(row["response"]) is str and _hash(row["response"]) == row["response_sha256"])
        projected,descriptor = project_output(row["response"]) if row["kind"] == "write" else (row["response"],None)
        if row["kind"] == "write": require(descriptor == row["output_projection"])
        pieces.append(projected)
    for page in p._doc.pages:
        for block in page.blocks:
            if block.block_id in p._tables:
                pieces.append("### 원문 표 · p. "+str(page.number)+"\n\n"+p._tables[block.block_id])
    from .pdf_chart_supplement import render_chart_supplement
    chart = _checked_chart_supplement(p)
    chart_text = render_chart_supplement(chart,expected_sha256=chart.identity()["sha256"],
        **_chart_render_options(p,chart))
    if chart_text: pieces.append(chart_text)
    supplement = _literal_supplement(p)
    if supplement.markdown: pieces.append(supplement.markdown)
    markdown = "\n\n".join(pieces)+"\n"
    manifest = _json(dict(version=VERSION,parent_plan_sha256=p.plan_sha256,
        policy_sha256=_hash(policy.encoded()),parent_inventory=inventory,
        outputs=[{k:v for k,v in row.items() if k != "response"} for row in active_results],
        exact_original_owner_union=True,all_literal_table_cells_appended=True,
        semantic_review_performed=False,semantic_completeness_proven=False,
        visual_accuracy_verified=False,content_sha256=_hash(markdown),
        chart_identity=chart.identity(),chart_rendered_sha256=_hash(chart_text),
        literal_table_count=supplement.table_count,literal_cell_count=supplement.cell_count,
        literal_tables_sha256=_hash(supplement.tables_json)))
    if len(markdown.encode()) > policy.max_markdown_bytes or len(manifest.encode()) > policy.max_manifest_bytes:
        raise AuthoringWorkLimitError("Continuation final retained Wiki exceeds its finite budget")
    return markdown,manifest
