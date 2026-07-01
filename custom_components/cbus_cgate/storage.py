"""Storage helpers for imported Toolkit projects."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import PROJECT_STORE_PREFIX, PROJECT_STORE_VERSION


def project_store(hass: HomeAssistant, key: str) -> Store[dict[str, Any]]:
    """Return a storage object for a normalised project."""
    return Store(hass, PROJECT_STORE_VERSION, f"{PROJECT_STORE_PREFIX}.{key}")


async def async_save_project(hass: HomeAssistant, project: dict[str, Any]) -> str:
    """Save a project and return its content-addressed key."""
    key = project["source_sha256"]
    await project_store(hass, key).async_save(project)
    return key


async def async_load_project(hass: HomeAssistant, key: str) -> dict[str, Any] | None:
    """Load a normalised project."""
    return await project_store(hass, key).async_load()


async def async_delete_project(hass: HomeAssistant, key: str) -> None:
    """Delete a normalised project."""
    await project_store(hass, key).async_remove()
