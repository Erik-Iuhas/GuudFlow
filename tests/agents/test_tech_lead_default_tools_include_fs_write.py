"""tech_lead default_tools and default_rbac_scopes include fs-write and fs-edit."""
from agents.builtin.tech_lead import TechLead


def test_fs_write_in_default_tools():
    assert "fs-write" in TechLead.default_tools
    assert "fs-edit" in TechLead.default_tools


def test_fs_write_in_default_rbac_scopes():
    assert "fs-write" in TechLead.default_rbac_scopes
    assert "fs-edit" in TechLead.default_rbac_scopes
