"""AddressResolver tests — mock the API client."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from loomcli.manifest.addressing import (
    AddressResolutionError,
    AddressResolver,
    resource_address,
)
from loomcli.manifest.parser import parse_manifest_text


def _resolver(fake_ou_tree):
    client = MagicMock()
    client.get = MagicMock(return_value=fake_ou_tree)
    return AddressResolver(client)


def test_ou_path_to_id_resolves(fake_ou_tree):
    r = _resolver(fake_ou_tree)
    assert r.ou_path_to_id("/dev-org") == "00000000-0000-0000-0000-00000000dddd"
    assert (
        r.ou_path_to_id("/dev-org/engineering")
        == "00000000-0000-0000-0000-0000000000aa"
    )


def test_ou_path_to_id_raises_on_unknown(fake_ou_tree):
    r = _resolver(fake_ou_tree)
    with pytest.raises(AddressResolutionError):
        r.ou_path_to_id("/does-not-exist")


def test_ou_path_to_id_trailing_slash_ok(fake_ou_tree):
    r = _resolver(fake_ou_tree)
    assert r.ou_path_to_id("/dev-org/engineering/") == r.ou_path_to_id("/dev-org/engineering")


def test_try_ou_path_to_id_returns_none(fake_ou_tree):
    r = _resolver(fake_ou_tree)
    assert r.try_ou_path_to_id("/nope") is None


def test_ou_id_to_path_round_trip(fake_ou_tree):
    r = _resolver(fake_ou_tree)
    path = "/dev-org/engineering"
    ou_id = r.ou_path_to_id(path)
    assert r.ou_id_to_path(ou_id) == path


def test_resource_address_ou():
    [r] = parse_manifest_text(
        "apiVersion: powerloom/v1\nkind: OU\nmetadata:\n  name: engineering\n  parent_ou_path: /dev-org\nspec:\n  display_name: X\n"
    )
    assert resource_address(r) == "/dev-org/engineering"


def test_resource_address_agent_includes_kind():
    [r] = parse_manifest_text(
        """
apiVersion: powerloom/v1
kind: Agent
metadata: { name: bot, ou_path: /dev-org/engineering }
spec:
  display_name: Bot
  model: claude-sonnet-4-6
  system_prompt: "be good"
  owner_principal_ref: user:admin@dev.local
"""
    )
    assert "bot" in resource_address(r)
    assert "Agent" in resource_address(r)


def test_find_in_ou_caches_list_result(fake_ou_tree):
    client = MagicMock()
    responses = {
        "/ous/tree": fake_ou_tree,
        "/skills": [{"id": "s1", "name": "foo", "ou_id": "ou1"}],
    }
    client.get = MagicMock(side_effect=lambda path, **kwargs: responses.get(path, []))
    r = AddressResolver(client)
    row1 = r.find_in_ou(list_path="/skills", ou_id="ou1", name="foo")
    row2 = r.find_in_ou(list_path="/skills", ou_id="ou1", name="foo")
    assert row1 == row2
    # /skills should have been called just once thanks to caching.
    skills_calls = [c for c in client.get.call_args_list if c.args[0] == "/skills"]
    assert len(skills_calls) == 1
