"""Home Assistant C-Gate add-on discovery and project-backup helpers."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

import aiohttp

CGATE_ADDON_SLUG = "cgate_server"
CGATE_ADDON_NAME = "C-Gate Server"
CGATE_ADDON_WEB_PORT = 8099
CGATE_ADDON_BACKUP_PATH = "/project/backup"
MAX_ADDON_PROJECT_BYTES = 64 * 1024 * 1024
_RUNNING_STATES = {"started", "startup"}


class AddonProjectError(RuntimeError):
    """Raised when a Toolkit backup cannot be obtained from the add-on."""


@dataclass(slots=True, frozen=True)
class DetectedCgateAddon:
    """A running C-Gate add-on that can be used by the config flow."""

    slug: str
    name: str
    host: str
    project_name: str
    state: str

    @property
    def backup_url(self) -> str:
        """Return the internal Supervisor-network project backup URL."""
        return f"http://{self.host}:{CGATE_ADDON_WEB_PORT}{CGATE_ADDON_BACKUP_PATH}"


def _state_text(value: Any) -> str:
    """Normalise Supervisor state strings and enum-like values."""
    return str(value or "").rsplit(".", 1)[-1].casefold()


def addon_info_to_dict(value: Any) -> dict[str, Any]:
    """Normalise an aiohasupervisor model or mapping to a plain dictionary."""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return result if isinstance(result, dict) else {}
    return {}


def is_cgate_addon(slug: str, info: dict[str, Any]) -> bool:
    """Return whether Supervisor metadata identifies the companion add-on."""
    normalised_slug = slug.casefold()
    name = str(info.get("name") or "").strip().casefold()
    return (
        normalised_slug == CGATE_ADDON_SLUG
        or normalised_slug.endswith(f"_{CGATE_ADDON_SLUG}")
        or name == CGATE_ADDON_NAME.casefold()
    )


def detect_cgate_addons(
    addons_info: dict[str, dict[str, Any] | Any | None],
) -> list[DetectedCgateAddon]:
    """Extract running C-Gate add-ons from Supervisor's add-on metadata."""
    detected: list[DetectedCgateAddon] = []
    for slug, raw_info in addons_info.items():
        info = addon_info_to_dict(raw_info)
        if not info or not is_cgate_addon(slug, info):
            continue
        state = _state_text(info.get("state"))
        if state not in _RUNNING_STATES:
            continue
        options = info.get("options")
        project_name = ""
        if isinstance(options, dict):
            project_name = str(options.get("project_name") or "").strip()
        detected.append(
            DetectedCgateAddon(
                slug=slug,
                name=str(info.get("name") or CGATE_ADDON_NAME).strip(),
                host=slug.replace("_", "-"),
                project_name=project_name,
                state=state,
            )
        )
    return sorted(detected, key=lambda addon: (addon.name.casefold(), addon.slug))


async def async_fetch_addon_project_backup(
    session: aiohttp.ClientSession,
    addon: DetectedCgateAddon,
) -> bytes:
    """Download the add-on's current CBZ backup over the internal app network."""
    timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=60)
    try:
        async with session.get(addon.backup_url, timeout=timeout) as response:
            if response.status != 200:
                detail = (await response.text(errors="replace"))[:300].strip()
                raise AddonProjectError(
                    f"C-Gate add-on returned HTTP {response.status}"
                    + (f": {detail}" if detail else "")
                )

            content_length = response.content_length
            if content_length is not None and content_length > MAX_ADDON_PROJECT_BYTES:
                raise AddonProjectError("C-Gate add-on project backup is too large")

            payload = bytearray()
            async for chunk in response.content.iter_chunked(1024 * 1024):
                payload.extend(chunk)
                if len(payload) > MAX_ADDON_PROJECT_BYTES:
                    raise AddonProjectError("C-Gate add-on project backup is too large")
    except AddonProjectError:
        raise
    except (TimeoutError, aiohttp.ClientError, OSError) as err:
        raise AddonProjectError(f"Unable to reach the C-Gate add-on: {err}") from err

    raw = bytes(payload)
    if not raw or not zipfile.is_zipfile(io.BytesIO(raw)):
        raise AddonProjectError("C-Gate add-on did not return a valid CBZ backup")
    return raw
