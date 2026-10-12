(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionDatabaseBridge = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ADOPTED_VIEW_TYPES = Object.freeze([
    "table", "board", "list", "gallery", "calendar", "timeline",
  ]);
  const ADOPTED_VIEW_TYPE_SET = new Set(ADOPTED_VIEW_TYPES);
  const ADOPTED_PROPERTY_TYPES = Object.freeze([
    "title", "text", "number", "checkbox", "date", "select", "multi_select",
    "status", "person", "files", "url", "email", "phone", "relation",
    "rollup", "formula", "created_time", "last_edited_time",
  ]);
  const ADOPTED_PROPERTY_TYPE_SET = new Set(ADOPTED_PROPERTY_TYPES);
  const READ_ONLY_PROPERTY_TYPES = new Set([
    "rollup", "formula", "created_time", "last_edited_time",
  ]);
  const VIEW_STATE_KEYS = Object.freeze([
    "filters", "sorts", "group_property", "month", "timeline_anchor",
    "timeline_scale", "timeline_date_property", "board_group_property",
    "calendar_date_property", "property_visibility",
  ]);

  function clone(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
    }
    return value;
  }

  function equal(left, right) {
    return JSON.stringify(stable(left)) === JSON.stringify(stable(right));
  }

  function propertyConfigFromUi(property) {
    const type = String(property?.type || "text");
    if (type === "relation") {
      return { target_database_id: String(property.relation_database_block_id || "") };
    }
    if (type === "rollup") {
      const raw = String(property.rollup_calculation || "show_unique");
      const aggregation = {
        show_original: "show_unique",
        count_unique: "show_unique",
        earliest: "min",
        latest: "max",
      }[raw] || raw;
      return {
        relation_property_id: String(property.rollup_relation_property || ""),
        target_property_id: String(property.rollup_target_property || ""),
        aggregation,
      };
    }
    if (type === "formula") return { expression: String(property.expression || "") };
    if (["select", "multi_select", "status"].includes(type)) {
      return { options: Array.isArray(property.options) ? clone(property.options) : [] };
    }
    return clone(property.config && typeof property.config === "object" ? property.config : {});
  }

  function propertyFromCanonical(property) {
    const config = clone(property?.config || {});
    const value = {
      id: String(property.id || ""),
      name: String(property.name || "Property"),
      type: String(property.type || "text"),
      _canonical_version: Number(property.version || 0),
      config,
    };
    if (value.type === "relation") {
      value.relation_database_block_id = String(config.target_database_id || "");
      value.relation_multiple = true;
    } else if (value.type === "rollup") {
      value.rollup_relation_property = String(config.relation_property_id || "");
      value.rollup_target_property = String(config.target_property_id || "");
      value.rollup_calculation = String(config.aggregation || "show_unique");
    } else if (value.type === "formula") {
      value.expression = String(config.expression || "");
    } else if (["select", "multi_select", "status"].includes(value.type)) {
      value.options = Array.isArray(config.options) ? clone(config.options) : [];
    }
    return value;
  }

  function viewFromCanonical(view) {
    return {
      id: String(view.id || ""),
      name: String(view.name || "View"),
      type: ADOPTED_VIEW_TYPE_SET.has(view.type) ? view.type : "table",
      ...clone(view.config || {}),
      _canonical_version: Number(view.version || 0),
    };
  }

  function rowFromCanonical(row) {
    return {
      ...clone(row.cells || {}),
      ...clone(row.computed || {}),
      page_id: String(row.id || ""),
      _canonical_row_id: String(row.id || ""),
      _canonical_row_version: Number(row.version || 0),
      _canonical_cell_versions: clone(row.cell_versions || {}),
    };
  }

  function applyViewState(props, view) {
    if (!view) return props;
    props.active_view_id = view.id;
    props.view = view.type;
    for (const key of VIEW_STATE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(view, key)) props[key] = clone(view[key]);
    }
    return props;
  }

  function canonicalToBlock(database, existingBlock) {
    const views = (database?.views || [])
      .filter(view => view?.state !== "archived" && ADOPTED_VIEW_TYPE_SET.has(view?.type))
      .map(viewFromCanonical);
    const currentViewId = String(existingBlock?.props?.active_view_id || "");
    const active = views.find(view => view.id === currentViewId) || views[0] || null;
    const baseProps = clone(existingBlock?.props || {});
    const props = {
      ...baseProps,
      database_id: String(database.id || ""),
      _canonical_version: Number(database.version || 0),
      properties: (database.properties || [])
        .filter(property => ADOPTED_PROPERTY_TYPE_SET.has(property?.type))
        .map(propertyFromCanonical),
      rows: (database.rows || []).map(rowFromCanonical),
      views,
      templates: clone(database.templates || []),
    };
    applyViewState(props, active);
    return {
      ...(existingBlock || {}),
      id: String(existingBlock?.id || database.id || ""),
      type: active?.type || (ADOPTED_VIEW_TYPE_SET.has(existingBlock?.type) ? existingBlock.type : "table"),
      content: String(database.title || existingBlock?.content || "Database"),
      props,
    };
  }

  function applyCanonicalDatabases(blocks, databases) {
    const byId = new Map((databases || []).map(database => [String(database.id || ""), database]));
    return (blocks || []).map(block => {
      const databaseId = String(block?.props?.database_id || block?.id || "");
      const database = byId.get(databaseId);
      return database ? canonicalToBlock(database, block) : block;
    });
  }

  function viewConfigFromUi(view) {
    const config = {};
    for (const key of VIEW_STATE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(view || {}, key)) config[key] = clone(view[key]);
    }
    return config;
  }

  function cellValue(property, item) {
    let value = item?.[property.id];
    if (property.type === "number") {
      if (value === "" || value == null) return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    if (property.type === "checkbox") return Boolean(value);
    if (property.type === "multi_select") {
      if (Array.isArray(value)) return value.map(String).filter(Boolean);
      return String(value || "").split(",").map(part => part.trim()).filter(Boolean);
    }
    if (property.type === "person") {
      if (Array.isArray(value)) return value.map(String).filter(Boolean);
      return value ? [String(value)] : [];
    }
    if (property.type === "relation") {
      if (Array.isArray(value)) return value.map(itemValue => String(itemValue?.page_id || itemValue || "")).filter(Boolean);
      return String(value || "").split(",").map(part => part.trim()).filter(Boolean);
    }
    if (property.type === "files") return Array.isArray(value) ? clone(value) : [];
    if (value == null) return null;
    return String(value);
  }

  function blockToDesired(block) {
    if (!ADOPTED_VIEW_TYPE_SET.has(block?.type)) return null;
    const databaseId = String(block?.props?.database_id || block.id || "");
    const properties = (block.props?.properties || [])
      .filter(property => property?.id && ADOPTED_PROPERTY_TYPE_SET.has(property.type))
      .map((property, position) => ({
        id: String(property.id),
        name: String(property.name || "Property"),
        type: String(property.type),
        position,
        config: propertyConfigFromUi(property),
      }));
    const views = (block.props?.views || [])
      .filter(view => view?.id && ADOPTED_VIEW_TYPE_SET.has(view.type))
      .map((view, position) => ({
        id: String(view.id),
        name: String(view.name || "View"),
        type: String(view.type),
        position,
        config: viewConfigFromUi(view),
      }));
    const rows = (block.props?.rows || []).map((item, position) => {
      const id = String(item?._canonical_row_id || item?.page_id || `${databaseId}:row:${position + 1}`);
      const cells = {};
      for (const property of properties) {
        if (!READ_ONLY_PROPERTY_TYPES.has(property.type)) cells[property.id] = cellValue(property, item || {});
      }
      return { id, position, cells };
    });
    return {
      id: databaseId,
      title: String(block.content || "Database"),
      properties,
      views,
      rows,
    };
  }

  function schemaPropertyPayload(property) {
    return { name: property.name, type: property.type, config: clone(property.config || {}) };
  }

  function viewPayload(view) {
    return { name: view.name, type: view.type, config: clone(view.config || {}) };
  }

  function action(commandKind, databaseId, targetId, payload, expectedTargetVersion) {
    return {
      command_kind: commandKind,
      database_id: databaseId,
      target_id: targetId,
      payload: clone(payload || {}),
      expected_target_version: expectedTargetVersion == null ? null : Number(expectedTargetVersion),
    };
  }

  function diffDatabase(canonical, desired) {
    if (!desired) return [];
    const actions = [];
    const databaseId = desired.id;
    if (!canonical) {
      actions.push(action("database.create", databaseId, databaseId, { title: desired.title }, 0));
      for (const property of desired.properties) {
        actions.push(action("database.property.create", databaseId, property.id, schemaPropertyPayload(property), 0));
      }
      for (const view of desired.views) {
        actions.push(action("database.view.create", databaseId, view.id, viewPayload(view), 0));
      }
      for (const row of desired.rows) {
        actions.push(action("database.row.create", databaseId, row.id, {}, 0));
        for (const property of desired.properties) {
          if (READ_ONLY_PROPERTY_TYPES.has(property.type)) continue;
          actions.push(action(
            "database.cell.set",
            databaseId,
            `${row.id}:${property.id}`,
            { row_id: row.id, property_id: property.id, value: clone(row.cells[property.id]) },
            0,
          ));
        }
      }
      return actions;
    }

    if (String(canonical.title || "") !== desired.title) {
      actions.push(action("database.update", databaseId, databaseId, { title: desired.title }, canonical.version));
    }
    const canonicalProperties = new Map((canonical.properties || []).map(item => [String(item.id), item]));
    const desiredProperties = new Map(desired.properties.map(item => [item.id, item]));
    for (const property of [...(canonical.properties || [])].reverse()) {
      if (!desiredProperties.has(String(property.id)) && property.type !== "title") {
        actions.push(action("database.property.delete", databaseId, property.id, {}, property.version));
      }
    }
    for (const property of desired.properties) {
      const current = canonicalProperties.get(property.id);
      if (!current) {
        actions.push(action("database.property.create", databaseId, property.id, schemaPropertyPayload(property), 0));
      } else if (!equal(
        { name: current.name, type: current.type, config: current.config || {} },
        schemaPropertyPayload(property),
      )) {
        actions.push(action("database.property.update", databaseId, property.id, schemaPropertyPayload(property), current.version));
      }
    }

    const canonicalViews = new Map((canonical.views || []).filter(item => item.state !== "archived").map(item => [String(item.id), item]));
    const desiredViews = new Map(desired.views.map(item => [item.id, item]));
    for (const view of canonicalViews.values()) {
      if (!desiredViews.has(String(view.id))) {
        actions.push(action("database.view.archive", databaseId, view.id, {}, view.version));
      }
    }
    for (const view of desired.views) {
      const current = canonicalViews.get(view.id);
      if (!current) {
        actions.push(action("database.view.create", databaseId, view.id, viewPayload(view), 0));
      } else if (!equal(
        { name: current.name, type: current.type, config: current.config || {} },
        viewPayload(view),
      )) {
        actions.push(action("database.view.update", databaseId, view.id, viewPayload(view), current.version));
      }
    }

    const canonicalRows = new Map((canonical.rows || []).map(item => [String(item.id), item]));
    const desiredRows = new Map(desired.rows.map(item => [item.id, item]));
    for (const row of desired.rows) {
      const current = canonicalRows.get(row.id);
      if (!current) actions.push(action("database.row.create", databaseId, row.id, {}, 0));
      const versions = current?.cell_versions || {};
      const values = current?.cells || {};
      for (const property of desired.properties) {
        if (READ_ONLY_PROPERTY_TYPES.has(property.type)) continue;
        const nextValue = row.cells[property.id];
        if (!current || !equal(values[property.id], nextValue)) {
          actions.push(action(
            "database.cell.set",
            databaseId,
            `${row.id}:${property.id}`,
            { row_id: row.id, property_id: property.id, value: clone(nextValue) },
            Number(versions[property.id] || 0),
          ));
        }
      }
    }
    for (const row of canonicalRows.values()) {
      if (!desiredRows.has(String(row.id))) {
        actions.push(action("database.row.delete", databaseId, row.id, {}, row.version));
      }
    }
    return actions;
  }

  function diffPage(blocks, databases) {
    const canonical = new Map((databases || []).map(item => [String(item.id || ""), item]));
    const actions = [];
    for (const block of blocks || []) {
      const desired = blockToDesired(block);
      if (!desired) continue;
      actions.push(...diffDatabase(canonical.get(desired.id) || null, desired));
    }
    return actions;
  }

  return Object.freeze({
    ADOPTED_PROPERTY_TYPES,
    ADOPTED_VIEW_TYPES,
    applyCanonicalDatabases,
    blockToDesired,
    canonicalToBlock,
    diffDatabase,
    diffPage,
  });
});
