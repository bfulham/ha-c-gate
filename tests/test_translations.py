"""Translation packaging regression tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "cbus_cgate"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_packaged_english_translation_matches_strings() -> None:
    """Custom integrations need a runtime translations/en.json copy."""
    assert _load(INTEGRATION / "translations" / "en.json") == _load(
        INTEGRATION / "strings.json"
    )


def test_all_menu_options_have_visible_labels() -> None:
    """Every async_show_menu option must have a translation label."""
    strings = _load(INTEGRATION / "strings.json")

    assert strings["config"]["step"]["user"]["menu_options"] == {
        "addon_project": "Use detected C-Gate add-on",
        "fetch_project": "Fetch from another C-Gate server",
        "upload_project": "Upload a project file",
    }
    assert strings["config"]["step"]["reconfigure"]["menu_options"] == {
        "reconfigure_addon": "Fetch from detected C-Gate add-on",
        "reconfigure_fetch": "Fetch latest project from another C-Gate server",
        "reconfigure_upload": "Upload a project file",
    }
    assert strings["options"]["step"]["init"]["menu_options"] == {
        "connections": "Hub connections",
        "applications": "Application mappings",
        "groups": "Group overrides",
        "performance": "Performance and discovery",
    }


def test_performance_flow_includes_groups_only_toggle() -> None:
    strings = _load(INTEGRATION / "strings.json")
    performance = strings["options"]["step"]["performance"]

    assert performance["data"]["hide_individual_fixtures"] == (
        "Hide individual fixtures and show groups only"
    )
