from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]
_MODULE = _ROOT / "static" / "admin_organization.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("SBE-6.2 administrator browser proof requires Node.js.")
    completed = subprocess.run(
        [node, "-e", source],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.scenario("HSPACE-015", "HSPACE-018", "AUTOEDGE-018")
def test_admin_higher_role_distribution_and_semantic_responses_fail_closed() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const api = require({json.dumps(str(_MODULE))});
const unit = '11111111-1111-4111-8111-111111111111';
const space = '22222222-2222-4222-8222-222222222222';
const assignment = '33333333-3333-4333-8333-333333333333';
const grant = '44444444-4444-4444-8444-444444444444';
const edge = '55555555-5555-4555-8555-555555555555';
const suppression = '66666666-6666-4666-8666-666666666666';
const request = '77777777-7777-4777-8777-777777777777';
const operation = '88888888-8888-4888-8888-888888888888';
const status = api.normalizeHigherRoleStatus({{
  assignee_display_name:'Leader',assignee_user_id:'leader',assignment_generation:3,
  assignment_id:assignment,assignment_row_version:2,org_unit_id:unit,request_id:request,
  role:'team_leader',space_generation:4,space_id:space,space_row_version:5,write_enabled:true,
}}, unit, 'team_leader');
assert.equal(status.assignment_id,assignment);
assert.equal(api.normalizeHigherRoleMutationResponse({{
  assignment_generation:null,assignment_id:null,assignment_row_version:null,
  operation_id:operation,org_unit_id:unit,request_id:request,space_generation:5,
  space_id:space,space_row_version:6,status:'ended',write_enabled:false,
}},operation).write_enabled,false);
assert.equal(api.normalizeDistributionMutationResponse({{
  grant_id:grant,operation_id:operation,request_id:request,revoked:true,row_version:2,
  source_space_id:space,status:'revoked',
}},operation).grant_id,grant);
assert.equal(api.normalizeSemanticCorrectionResponse({{
  edge_id:edge,edge_kind:'auto',edge_status:'suppressed',operation_id:operation,
  reevaluation_queued:false,request_id:request,row_version:2,scope_space_id:space,
  suppression_id:suppression,suppression_status:'active',
}},operation).edge_id,edge);
let code = null;
try {{ api.normalizeHigherRoleMutationResponse({{
  assignment_generation:null,assignment_id:null,assignment_row_version:null,
  operation_id:operation,org_unit_id:unit,request_id:request,space_generation:5,
  space_id:space,space_row_version:6,status:'ended',write_enabled:true,
}},operation); }} catch(error) {{ code=error.code; }}
assert.equal(code,'INVALID_HIGHER_ROLE_MUTATION');
console.log(JSON.stringify({{code,role:status.role}}));
"""
    )
    assert result == {"code": "INVALID_HIGHER_ROLE_MUTATION", "role": "team_leader"}


def test_admin_source_keeps_break_glass_phrases_and_canonical_routes_explicit() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert "REVOKE DISTRIBUTION ${grant.grant_id}" in source
    assert "SUPPRESS ORGANIZATIONAL SEMANTIC EDGE" in source
    assert "/distributions/${encodeURIComponent(grant.grant_id)}/revoke" in source
    assert "/edges/${encodeURIComponent(edgeId)}/suppress" in source
    assert 'responseKind: "higher_role"' in source
    assert 'responseKind: "distribution"' in source
    assert 'responseKind: "semantic_correction"' in source
