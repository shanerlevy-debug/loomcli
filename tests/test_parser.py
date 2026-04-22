"""Manifest parser tests — pure unit, no network."""
from __future__ import annotations

import pytest

from loomcli.manifest import parse_manifest_text
from loomcli.manifest.parser import ManifestParseError


GOOD_SINGLE = """
apiVersion: powerloom/v1
kind: OU
metadata:
  name: engineering
  parent_ou_path: /dev-org
spec:
  display_name: Engineering
"""

GOOD_MULTI = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: Engineering }
---
apiVersion: powerloom/v1
kind: Skill
metadata: { name: python-lint, ou_path: /dev-org/engineering }
spec:
  display_name: Python lint
  description: lints Python
"""

AGENT_WITH_ATTACHMENTS = """
apiVersion: powerloom/v1
kind: Agent
metadata: { name: code-reviewer, ou_path: /dev-org/engineering }
spec:
  display_name: Code Reviewer
  model: claude-sonnet-4-6
  system_prompt: "Review code."
  owner_principal_ref: user:admin@dev.local
  skills: [python-lint]
  mcp_servers: [reports-files]
"""


def test_parses_single_ou():
    resources = parse_manifest_text(GOOD_SINGLE)
    assert len(resources) == 1
    r = resources[0]
    assert r.kind == "OU"
    assert r.metadata.name == "engineering"
    assert r.metadata.parent_ou_path == "/dev-org"
    assert r.spec.display_name == "Engineering"


def test_parses_multi_doc():
    resources = parse_manifest_text(GOOD_MULTI)
    assert [r.kind for r in resources] == ["OU", "Skill"]


def test_empty_document_skipped():
    text = "---\n" + GOOD_SINGLE + "\n---\n"
    resources = parse_manifest_text(text)
    assert len(resources) == 1


def test_rejects_wrong_api_version():
    """Schema's apiVersion enum rejects unknown values."""
    bad = GOOD_SINGLE.replace("powerloom/v1", "powerloom/v0")
    with pytest.raises(ManifestParseError, match="apiVersion.*is not one of"):
        parse_manifest_text(bad)


def test_accepts_canonical_api_version():
    """Schema accepts both 'powerloom/v1' (legacy) and 'powerloom.app/v1'
    (canonical) — back-compat for v009-v024 manifests."""
    canonical = GOOD_SINGLE.replace("powerloom/v1", "powerloom.app/v1")
    resources = parse_manifest_text(canonical)
    assert resources[0].kind == "OU"


def test_rejects_unknown_kind():
    bad = GOOD_SINGLE.replace("kind: OU", "kind: NotAThing")
    with pytest.raises(ManifestParseError, match="unknown kind"):
        parse_manifest_text(bad)


def test_rejects_unexpected_top_level_key():
    """Schema's top-level additionalProperties: false rejects extraneous keys."""
    bad = GOOD_SINGLE + "\nextraneous: surprise\n"
    with pytest.raises(ManifestParseError, match="Additional properties are not allowed"):
        parse_manifest_text(bad)


def test_rejects_unknown_metadata_field():
    """Schema's metadata.additionalProperties: false catches unknown fields."""
    bad = GOOD_SINGLE.replace(
        "  name: engineering\n",
        "  name: engineering\n  surprise_field: yep\n",
    )
    with pytest.raises(ManifestParseError, match="Additional properties are not allowed"):
        parse_manifest_text(bad)


def test_agent_with_attachments_parses():
    resources = parse_manifest_text(AGENT_WITH_ATTACHMENTS)
    assert len(resources) == 1
    r = resources[0]
    assert r.kind == "Agent"
    assert r.spec.skills == ["python-lint"]
    assert r.spec.mcp_servers == ["reports-files"]


def test_parse_error_mentions_source():
    with pytest.raises(ManifestParseError) as ei:
        parse_manifest_text("bad: yaml: :", source="foo.yaml")
    assert "foo.yaml" in str(ei.value)


def test_mcp_deployment_shape():
    text = """
apiVersion: powerloom/v1
kind: MCPDeployment
metadata: { name: reports-files, ou_path: /dev-org/engineering }
spec:
  display_name: Reports
  template_kind: files
  config: { s3_bucket: acme-reports }
  policy:
    path_allowlist: ["/reports/**"]
    allowed_operations: [read, list]
    max_file_size_mb: 10
"""
    resources = parse_manifest_text(text)
    r = resources[0]
    assert r.spec.template_kind == "files"
    assert r.spec.config["s3_bucket"] == "acme-reports"
    assert r.spec.policy["allowed_operations"] == ["read", "list"]


def test_role_binding_metadata_validates_decision_type():
    text = """
apiVersion: powerloom/v1
kind: RoleBinding
metadata:
  principal_ref: user:jane@dev.local
  role: AgentViewer
  scope_ou_path: /dev-org/engineering
  decision_type: allow
spec: {}
"""
    resources = parse_manifest_text(text)
    assert resources[0].metadata.decision_type == "allow"
