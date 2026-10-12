"""Restore a writer-ready source from the exact authorized v42 page archive.

Cold paths re-extract and bind geometry to the retained original in the existing
isolated worker. An eligible ordinary source can reuse its service-local privately
admitted plan only after fresh raw/archive/asset/configuration and scope checks.
Reuse, never rerender, archived page pixels. Private synchronous run restoration
may reuse a physical read-only handle through the existing lexical lifecycle;
every read retains its own fresh guards and transaction. Public restoration stays
independent. No model, publication or durable-job mutation is introduced here.
"""
from dataclasses import asdict, fields, replace
from hashlib import sha256
import hmac
import json

from ...document_pdf_process import PdfProcessError
from ..db import ConnectionFactory, TransactionMode, transaction
from ..pdf_geometry_authoring import prepare_pdf_source
from ..pdf_visual_source import PreparedPdfVisualSource, PdfPageRenderPolicy, fitted_page_render_policy, checked_visual_source, renderer_identity, _seal as _visual_seal
from ..pdf_visual_authoring import PdfVisualPlan, VERSION_V12, VERSION_V13, VERSION_V14, VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, prompt_for_policy, _request as _visual_request, _inventory_batch_rows
from ..migrations.v095_pdf_numeric_append import POLICY_IDENTITIES
from ..pdf_native_figure_source import prepare_native_figures
from ..pdf_visual_authoring import VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36, ATOMIC_REQUEST_PARTITION_V14
from ..pdf_layout_source import prepare_layout
from ..pdf_chart_preparation import (VERSION as CHART_PREPARATION_VERSION,
    PdfChartPreparationLimits, restore_pdf_chart_supplement)
from ..pdf_figure_roi_source import VERSION as ROI_INPUT_VERSION, prepare_figure_rois, figure_roi_group_identity
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, _hash, _json
from ..wiki_preservation import PreservationExecutionPolicy
from ..repositories.pdf_visual_archive import (
    PdfVisualArchiveRepository, PdfVisualArchiveConflict, ArchivedPdfVisualBytes, _FIELDS, _chart_descriptor,
)
from ..repositories.pdf_visual_authoring import prepare_visual_run, _prepare_visual_run_from_reuse
from .native_authoring_source import _Leases
from .pdf_visual_plan_reuse import _PdfVisualPlanReuse, PdfVisualPlanReuseConflict, _reuse_key
from .pdf_visual_authoring_source import (
    CanonicalPdfVisualSourceService, PreparedCanonicalPdfVisualSource,
    PDF_VISUAL_PREPARATION_VERSION, _seal as _canonical_seal, _require_numeric_append_catalog,
    _require_atomic_partition_catalog,
)


class PdfVisualRestoreConflict(PdfVisualArchiveConflict):
    """Sanitized incompatible archive/current source, never an alternate plan."""


def _require(value, message="The saved visual PDF plan differs from its current source."):
    if not value:
        raise PdfVisualRestoreConflict(message)


def _cancel(event):
    if event is not None and event.is_set():
        raise PdfProcessError("CANCELLED")


def _canonical_object(text):
    _require(type(text) is str and 0 < len(text.encode()) <= 1_048_576)
    result = json.loads(text)
    _require(type(result) is dict and _json(result) == text)
    return result


def _policy(value, cls):
    _require(type(value) is dict and set(value) == {f.name for f in fields(cls)})
    data = dict(value)
    if cls is AuthoringModelPolicy:
        for key in ("provider_order", "model_ids"):
            if data[key] is not None:
                _require(type(data[key]) is list)
                data[key] = tuple(data[key])
        if data["wire_models"] is not None:
            _require(type(data["wire_models"]) is list
                and all(type(p) is list and len(p) == 2 for p in data["wire_models"]))
            data["wire_models"] = tuple(tuple(p) for p in data["wire_models"])
    result = cls(**data)
    # JSON equality distinguishes true/1 and numbers/numeric strings too.
    _require(_json(asdict(result)) == _json(value))
    return result


def _configuration(service, bundle, source_extension=None):
    try:
        config = _canonical_object(bundle["configuration_json"])
        if config.get('policy_version') == VERSION_V36:
            _require_numeric_append_catalog(service._factory)
        expected_keys = {"version", "policy_version", "prompt_sha256", "chunk_bytes",
            "model_policy", "work_policy", "execution_policy", "geometry_limits", "process_policy",
            "render_policy", "renderer_identity", "retained_asset_policy"}
        forward = config.get("forward_source_policy")
        if 'request_partition_policy' in config:
            _require_atomic_partition_catalog(service._factory)
            expected_keys.add('request_partition_policy')
            _require(source_extension is None and config.get('policy_version') == VERSION_V36
                and forward is not None and type(config['request_partition_policy']) is str
                and config['request_partition_policy'] == ATOMIC_REQUEST_PARTITION_V14,
                'Stored original-range partition differs from its forward binding.')
        if forward is not None:
            expected_keys.add("forward_source_policy")
            _require(source_extension is None and config.get("policy_version") == VERSION_V36
                and _json(forward) == _json({"version":"pdf-chart-admitted-owned-native-v13",
                    "configured_render_policy":asdict(service._render_policy),
                    "configured_request_budget_bytes":1_114_112,
                    "effective_request_budget_bytes":4_194_304,
                    "configured_logical_input_bytes":67_108_864,
                    "effective_logical_input_bytes":134_217_728,
                    "all_pages_retained":True,"grid_fidelity_proven":False}),
                "Fresh ordinary source policy differs from its saved binding.")
            effective = PdfPageRenderPolicy(**config["render_policy"])
            _require(72 <= effective.dpi <= service._render_policy.dpi
                and asdict(replace(effective,dpi=service._render_policy.dpi)) == asdict(service._render_policy)
                and _json(bundle["source"]["render_policy"]) == _json(config["render_policy"]),
                "Saved forward render canvas policy differs.")
            _require(config["model_policy"]["request_budget_bytes"] == 4_194_304
                and config["model_policy"]["max_tokens"] == 4096,
                "Saved forward byte/output reservation differs.")
            _require(type(config["work_policy"]["logical_input_bytes"]) is int
                and config["work_policy"]["logical_input_bytes"] == 134_217_728,
                "Saved forward complete-document transport reservation differs.")
        if config.get('policy_version') in (VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36):
            expected_keys.add('layout_identity')
            _require(type(config.get('layout_identity')) is dict
                and 'native_figures' not in config['layout_identity'])
        if config.get('policy_version') in (VERSION_V34, VERSION_V35, VERSION_V36):
            expected_keys.update(('chart_identity','chart_preparation'))
            _require(type(service._chart_limits) is PdfChartPreparationLimits
                and _json(config.get('chart_preparation')) == _json({'version':CHART_PREPARATION_VERSION,
                    'language':service._chart_ocr_language,'limits':asdict(service._chart_limits)})
                and _chart_descriptor(bundle) == config.get('chart_identity'),
                'Saved chart preparation differs from current explicit server policy.')
        if config.get("policy_version") in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
            expected_keys.add("layout_identity")
            _require(type(config.get("layout_identity")) is dict)
        if config.get("policy_version") in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
            expected_keys.add("figure_roi_identity")
            roi = config.get("figure_roi_identity")
            _require(type(roi) is dict and set(roi) == {'version','source_raw_sha256','layout_catalog_sha256',
                'dpi','limits','rois'} and roi['version'] == ROI_INPUT_VERSION and type(roi['rois']) is list)
        _require(set(config) == (expected_keys | ({"protected_binding"} if source_extension else set())))
        if source_extension is not None:
            binding = config["protected_binding"]
            _require(type(binding) is dict and binding.get("version") == "authorized-pdf-original-derivative-visual-v1"
                and binding.get("original_sha256") == source_extension["original_sha256"]
                and binding.get("derivative_sha256") == source_extension["derivative_sha256"]
                and _json(binding.get("derivative_visual_source")) == _json(bundle["source"]),
                "The saved protected PDF binding differs from its source.")
        _require((config["policy_version"], config["prompt_sha256"]) in POLICY_IDENTITIES)
        expected = {"version": PDF_VISUAL_PREPARATION_VERSION,
            "prompt_sha256": _hash(prompt_for_policy(config["policy_version"])), "geometry_limits": asdict(service._limits),
            "process_policy": asdict(service._process_policy), "render_policy": config["render_policy"]
                if forward is not None else asdict(service._render_policy),
            "renderer_identity": renderer_identity(), "retained_asset_policy": asdict(service._asset_policy)}
        _require(all(_json(config[key]) == _json(value) for key, value in expected.items()),
            "Saved visual configuration does not match the current server-owned policy.")
        _require(type(config["chunk_bytes"]) is int and 4 <= config["chunk_bytes"] <= 8192)
        return (config, _policy(config["model_policy"], AuthoringModelPolicy),
            _policy(config["work_policy"], AuthoringWorkPolicy),
            _policy(config["execution_policy"], PreservationExecutionPolicy))
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PdfVisualRestoreConflict("Stored visual PDF configuration is unavailable or incompatible.") from exc


def restore_pdf_visual_source(source_service, archive_repository, *, preview_id, actor_user_id, cancel_event=None):
    """Return a new process-local preparation; caller must revalidate before effects.

Only the source service and its own archive/stager may restore. File leases for
the original, retained assets, geometry and PNGs survive until result.close().
The archive is not an author run: replaying this method never authorizes a new
or repeated model call without the future owning workflow's attempt fence.
"""
    return _restore_pdf_visual_source(source_service, archive_repository,
        preview_id=preview_id, actor_user_id=actor_user_id, cancel_event=cancel_event)


def _restore_pdf_visual_run(source_service, archive_repository, *, preview_id, actor_user_id,
        source_archive_id, max_executions=2, cancel_event=None):
    """Private synchronous restore/run preparation; never an execution authority.

    Private run reads may share an existing lexical read-only lifecycle, including
    historical/protected runs. Only eligible ordinary plans share their complete
    inventory; public restoration and compilation/authority rules stay unchanged.
    """
    return _restore_pdf_visual_source(source_service, archive_repository,
        preview_id=preview_id, actor_user_id=actor_user_id, cancel_event=cancel_event,
        _run_options=(source_archive_id, max_executions))


def _restore_pdf_visual_source(source_service, archive_repository, *, preview_id, actor_user_id,
        cancel_event=None, _run_options=None, _read_lifecycle=None):
    _require(type(source_service) is CanonicalPdfVisualSourceService
        and type(archive_repository) is PdfVisualArchiveRepository
        and archive_repository.source_service is source_service
        and archive_repository.stager is source_service._stager,
        "Restore requires the same canonical source service and archive stager.")
    _cancel(cancel_event)
    if (_run_options is not None and _read_lifecycle is None
            and type(getattr(source_service, "_factory", None)) is ConnectionFactory):
        # Private synchronous writers share a physical RO handle, never a read
        # snapshot. Every borrow gets fresh guards, a wrapper and its own TX.
        restored_run = None
        try:
            with source_service._factory._read_only_lifecycle() as lifecycle:
                restored_run = _restore_pdf_visual_source(source_service, archive_repository,
                    preview_id=preview_id, actor_user_id=actor_user_id, cancel_event=cancel_event,
                    _run_options=_run_options, _read_lifecycle=lifecycle)
            return restored_run
        except BaseException as primary:
            # A physical-close failure must not strand an otherwise ready source.
            if restored_run is not None:
                try:
                    restored_run.source.close()
                except BaseException as cleanup:
                    primary.add_note("Restored visual source cleanup also failed: " + type(cleanup).__name__)
            raise

    def read_source():
        if (_read_lifecycle is None or getattr(source_service._read, "__func__", None)
                is not CanonicalPdfVisualSourceService._read):
            return source_service._read(preview_id, actor_user_id, now_ms=0)
        with _read_lifecycle.borrow() as connection:
            source_service._factory._check_owned_read_only(connection)
            with transaction(connection, TransactionMode.DEFERRED):
                return source_service._snapshot(connection, preview_id, actor_user_id, now_ms=0)

    def read_archive():
        if (_read_lifecycle is None or getattr(archive_repository._load, "__func__", None)
                is not PdfVisualArchiveRepository._load):
            return archive_repository._load(preview_id, actor_user_id)
        with _read_lifecycle.borrow() as connection:
            source_service._factory._check_owned_read_only(connection)
            with transaction(connection, TransactionMode.DEFERRED):
                return archive_repository.load(connection,
                    preview_id=preview_id, actor_user_id=actor_user_id)

    before = read_source()
    _cancel(cancel_event)
    leases, group, result, run, inventory_hit = [], None, None, None, False
    try:
        saved = archive_repository.read(preview_id=preview_id, actor_user_id=actor_user_id, cancel_event=cancel_event)
        if callable(getattr(saved, "close", None)):
            leases.append(saved)
        _require(type(saved) is ArchivedPdfVisualBytes and saved._leases.owner is archive_repository
            and saved._leases.token in archive_repository._active)
        _cancel(cancel_event)
        metadata = dict(zip(_FIELDS, saved.metadata, strict=True))
        _require(metadata["preview_id"] == preview_id and metadata["actor_user_id"] == actor_user_id
            and metadata["source_fence_sha256"] == before.fence_sha256
            and metadata["artifact_id"] == before.source["id"]
            and metadata["extraction_snapshot_id"] == before.extraction["id"]
            and metadata["parent_job_id"] == before.preview["durable_job_id"])
        _require(type(saved.bundle_json) is str and _hash(saved.bundle_json) == metadata["bundle_sha256"]
            and len(saved.bundle_json.encode()) == metadata["bundle_size_bytes"]
            and type(saved.geometry) is bytes and len(saved.geometry) == metadata["geometry_size_bytes"]
            and sha256(saved.geometry).hexdigest() == metadata["geometry_sha256"])
        bundle = _canonical_object(saved.bundle_json)
        config, model, work, execution = _configuration(source_service, bundle, before.source_extension)
        if config['policy_version'] not in (VERSION_V34, VERSION_V35, VERSION_V36):
            _require(saved.chart_supplement is None)
        retained = _json([{k: asset[k] for k in
            ("id", "ordinal", "logical_name", "media_type", "content_sha256", "size_bytes")}
            for asset in before.assets])
        _require(bundle["retained_assets_json"] == retained)
        _cancel(cancel_event)
        original = source_service._loader.load(artifact_id=before.source["id"],
            expected_sha256=before.source["raw_sha256"], expected_size=before.source["raw_size_bytes"],
            expected_media_type=before.source["media_type"])
        leases.append(original.lease)
        _require(original.original_filename == before.source["original_filename"])
        for asset in before.assets:
            _cancel(cancel_event)
            source_service._verify_retained_asset(asset, leases)
        _cancel(cancel_event)
        _require(read_source().fence_sha256 == before.fence_sha256)
        _cancel(cancel_event)
        reuse = None
        reuse_key = None
        plan = None
        if config["policy_version"] in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36) and before.source_extension is None:
            reuse = source_service._visual_plan_reuse
            _require(type(reuse) is _PdfVisualPlanReuse and reuse.owner is source_service,
                "Ordinary PDF plan reuse belongs to another source service.")
            try:
                reuse_key = _reuse_key(source_service, before, saved, original, retained)
                plan = reuse.get(source_service, reuse_key, geometry=saved.geometry, pages=saved.pages,
                    **({'chart_supplement': saved.chart_supplement}
                       if config['policy_version'] in (VERSION_V34, VERSION_V35, VERSION_V36) else {}))
            except PdfVisualPlanReuseConflict as exc:
                raise PdfVisualRestoreConflict("Prepared PDF reuse differs from its current source.") from exc
            _cancel(cancel_event)
        reused = plan is not None
        if not reused:
            parser_input = original.content
            opened = None
            if before.source_extension is not None:
                _require(source_service._protected_pdf_opener is not None,
                    "Protected PDF restore lacks an authorized opener.")
                opened = source_service._protected_pdf_opener.open(original.original_filename, original.content)
                coverage = json.loads(before.source_extension["coverage_json"])
                _require(opened.original_sha256 == before.source["raw_sha256"]
                    and opened.derivative_sha256 == before.source_extension["derivative_sha256"]
                    and opened.expected_pages == before.source_extension["expected_locations"]
                    and opened.visual_pages == tuple(row["ordinal"] for row in coverage if row["visual_present"]),
                    "Protected PDF restore derivative differs from retained provenance.")
                parser_input = opened.derivative_pdf
            geometry = prepare_pdf_source(before.extraction["id"], original.original_filename, parser_input,
                saved_manifest=saved.geometry, python_executable=source_service._python,
                limits=source_service._limits, process_policy=source_service._process_policy,
                cancel_event=cancel_event, allow_visual_only=True,
                allow_ambiguous_grid=(before.source_extension is None
                    and bundle['source']['geometry']['geometry_version']
                        == 'pdf-original-glyphs-ambiguous-grid-v1'))
            _require(geometry.geometry_json == saved.geometry
                and _json(geometry.identity()) == _json(bundle["source"]["geometry"]))
            _cancel(cancel_event)
            effective_render = PdfPageRenderPolicy(**config["render_policy"])
            if "forward_source_policy" in config:
                from ..pdf_geometry_authoring import _document
                _require(effective_render == fitted_page_render_policy(_document(geometry),source_service._render_policy),
                    "Saved complete-document DPI no longer matches its original.")
            visual = PreparedPdfVisualSource(geometry, saved.pages, effective_render, "")
            _require(all(p.image.source_id == "PV_" + _hash(_json([geometry.raw_sha256, p.page,
                sha256(p.image.data).hexdigest(), asdict(effective_render)])) for p in saved.pages))
            visual = replace(visual, seal=_visual_seal(visual))
            checked_visual_source(visual)
            _require(_json(visual.identity()) == _json(bundle["source"]))
            if opened is not None:
                expected_binding = {"version":"authorized-pdf-original-derivative-visual-v1",
                    "original_sha256":opened.original_sha256,"derivative_sha256":opened.derivative_sha256,
                    "original_filename":original.original_filename,"opening_method":opened.opening_method,
                    "expected_pages":opened.expected_pages,"visual_pages":list(opened.visual_pages),
                    "derivative_visual_source":visual.identity()}
                _require(_json(expected_binding) == _json(config["protected_binding"]),
                    "Protected PDF visual archive lost its original/derivative binding.")
            layout_source = None
            figure_rois = None
            chart_supplement = None
            if config['policy_version'] in (VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36):
                _cancel(cancel_event)
                layout_source = prepare_layout(visual,parser_input,python_executable=source_service._python,
                    process_policy=source_service._process_policy,cancel_event=cancel_event)
                _require(_json(layout_source.identity()) == _json(config['layout_identity']),
                    'Saved literal table source differs from the verified original.')
                _cancel(cancel_event)
                if config['policy_version'] in (VERSION_V34, VERSION_V35, VERSION_V36):
                    chart_supplement = restore_pdf_chart_supplement(layout_source,parser_input,saved.chart_supplement,
                        expected_identity=config['chart_identity'],python_executable=source_service._python,
                        language=source_service._chart_ocr_language,limits=source_service._chart_limits,
                        process_policy=source_service._process_policy,cancel_event=cancel_event)
                    _cancel(cancel_event)
            if config["policy_version"] in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
                # The cold path derives capabilities from the verified original
                # / authorized clear derivative and saved page pixels outside SQL.
                _cancel(cancel_event)
                native = prepare_native_figures(visual,parser_input,python_executable=source_service._python,
                    process_policy=source_service._process_policy,cancel_event=cancel_event)
                _cancel(cancel_event)
                layout_source = prepare_layout(visual,parser_input,python_executable=source_service._python,
                    process_policy=source_service._process_policy,cancel_event=cancel_event,native_figures=native)
                _require(_json(layout_source.identity()) == _json(config["layout_identity"]),
                    "Saved PDF layout/native source differs from the verified original.")
                _cancel(cancel_event)
                if config['policy_version'] in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
                    figure_rois = prepare_figure_rois(layout_source,parser_input,python_executable=source_service._python,
                        process_policy=source_service._process_policy,cancel_event=cancel_event)
                    _require(_json(figure_roi_group_identity(layout_source,figure_rois)) == _json(config['figure_roi_identity']),
                        'Saved PDF appearance ROI differs from the verified source.')
                    _cancel(cancel_event)
            plan = PdfVisualPlan(visual, config["chunk_bytes"], model, work, execution,
                policy_version=config["policy_version"],
                **({"layout_source":layout_source} if layout_source is not None else {}),
                **({'chart_supplement':chart_supplement} if config['policy_version'] in (VERSION_V34, VERSION_V35, VERSION_V36) else {}),
                **({'source_definitions':True} if config['policy_version'] == VERSION_V35 else {}),
                **({'source_definitions':False} if config['policy_version'] == VERSION_V36 else {}),
                **({'request_partition_policy':config['request_partition_policy']}
                    if 'request_partition_policy' in config else {}),
                **({'figure_rois':figure_rois} if config['policy_version'] in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32) else {}))
        # V24 ordinary PDFs compile every exact request between fresh entry
        # and exit plan checks. Protected and historical sources retain their
        # selected-request path; current source/archive checks below stay live.
        _require(plan.plan_sha256 == metadata["plan_sha256"] == bundle["plan_sha256"])
        share_inventory = (_run_options is not None and plan.policy_version in (VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36)
            and before.source_extension is None)
        if share_inventory:
            _require(metadata["id"] == _run_options[0])
            group = _Leases(source_service, leases)
            result = PreparedCanonicalPdfVisualSource(preview_id, actor_user_id, before.preview["durable_job_id"],
                before.source["id"], plan, before.fence_sha256, bundle["configuration_json"], retained, "", group)
            result = replace(result, seal=_canonical_seal(result))
            _cancel(cancel_event)
            if plan.policy_version in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) and reused:
                try:
                    run, inventory_hit = _prepare_visual_run_from_reuse(result,
                        source_archive_id=_run_options[0], max_executions=_run_options[1], reuse=reuse, key=reuse_key)
                except PdfVisualPlanReuseConflict as exc:
                    raise PdfVisualRestoreConflict("Prepared PDF inventory differs from its current source.") from exc
            else:
                run = prepare_visual_run(result, source_archive_id=_run_options[0], max_executions=_run_options[1])
            request_digests = [row[3] for row in run.batches]
        else:
            request_digests = ([row[3] for row in _inventory_batch_rows(plan)]
                if plan.policy_version in (VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36) and before.source_extension is None
                else [_visual_request(plan, b)[2] for b in plan.batches])
        _require(request_digests == bundle["requests"])
        _cancel(cancel_event)
        _require(read_source().fence_sha256 == before.fence_sha256)
        expected_pages = tuple((p.page, p.image.source_id, p.width, p.height,
            p.identity()["sha256"], len(p.image.data)) for p in saved.pages)
        _require(read_archive() ==
            (saved.metadata, saved.bundle_json, expected_pages))
        _cancel(cancel_event)
        if result is None:
            group = _Leases(source_service, leases)
            result = PreparedCanonicalPdfVisualSource(preview_id, actor_user_id, before.preview["durable_job_id"],
                before.source["id"], plan, before.fence_sha256, bundle["configuration_json"], retained, "", group)
            result = replace(result, seal=_canonical_seal(result))
        else:
            # Run preparation precedes the fresh exit scope/archive checks only
            # on this private path; verify its source again after those checks.
            _require(result.plan is plan and hmac.compare_digest(result.seal, _canonical_seal(result)),
                "Prepared visual source changed during synchronous run restoration.")
        _cancel(cancel_event)
        if reuse is not None and (not reused or (share_inventory and plan.policy_version in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) and not inventory_hit)):
            reuse.put(source_service, reuse_key, plan,
                **({'run': run} if share_inventory and plan.policy_version in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) else {}))
        if _run_options is not None:
            if run is None:
                run = prepare_visual_run(result, source_archive_id=_run_options[0], max_executions=_run_options[1])
            return run
        return result
    except BaseException:
        if group is not None:
            try:
                group.close()
            except BaseException:
                pass
        else:
            for lease in reversed(leases):
                try:
                    lease.close()
                except BaseException:
                    pass
        raise
