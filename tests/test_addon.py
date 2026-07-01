"""Tests for Home Assistant add-on discovery."""

from __future__ import annotations

from addon import DetectedCgateAddon, addon_info_to_dict, detect_cgate_addons, is_cgate_addon


def test_detects_running_repository_prefixed_addon() -> None:
    addons = detect_cgate_addons(
        {
            "abc123_cgate_server": {
                "name": "C-Gate Server",
                "state": "started",
                "options": {"project_name": "THEBEND"},
            },
            "other": {
                "name": "Other add-on",
                "state": "started",
                "options": {},
            },
        }
    )

    assert len(addons) == 1
    assert addons[0].slug == "abc123_cgate_server"
    assert addons[0].host == "abc123-cgate-server"
    assert addons[0].project_name == "THEBEND"


def test_ignores_stopped_cgate_addon() -> None:
    assert (
        detect_cgate_addons(
            {
                "cgate_server": {
                    "name": "C-Gate Server",
                    "state": "stopped",
                    "options": {"project_name": "TEST"},
                }
            }
        )
        == []
    )


def test_name_match_supports_renamed_slug() -> None:
    assert is_cgate_addon("local_custom_slug", {"name": "C-Gate Server"})


class _AddonModel:
    def to_dict(self) -> dict[str, object]:
        return {
            "slug": "local_cgate_server",
            "name": "C-Gate Server",
            "state": "started",
            "options": {"project_name": "THEBEND"},
        }


def test_detects_aiohasupervisor_model_and_builds_backup_url() -> None:
    model = _AddonModel()
    assert addon_info_to_dict(model)["slug"] == "local_cgate_server"
    addons = detect_cgate_addons({"local_cgate_server": model})
    assert addons == [
        DetectedCgateAddon(
            slug="local_cgate_server",
            name="C-Gate Server",
            host="local-cgate-server",
            project_name="THEBEND",
            state="started",
        )
    ]
    assert addons[0].backup_url == ("http://local-cgate-server:8099/project/backup")


def test_downloads_valid_project_backup() -> None:
    import asyncio
    import io
    import zipfile

    from addon import async_fetch_addon_project_backup

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("TEST.xml", "<Project><TagName>TEST</TagName></Project>")
    payload = archive_buffer.getvalue()

    class _Content:
        async def iter_chunked(self, _size: int):
            yield payload[:10]
            yield payload[10:]

    class _Response:
        status = 200
        content_length = len(payload)
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _Session:
        def get(self, url: str, *, timeout):
            assert url == "http://local-cgate-server:8099/project/backup"
            assert timeout.total == 90
            return _Response()

    addon = DetectedCgateAddon(
        slug="local_cgate_server",
        name="C-Gate Server",
        host="local-cgate-server",
        project_name="TEST",
        state="started",
    )
    assert asyncio.run(async_fetch_addon_project_backup(_Session(), addon)) == payload
