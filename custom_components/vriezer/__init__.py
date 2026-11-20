"""Freezer inventory: packages with a unique code and label, partial removal, QR scans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .manager import Manager, split_line

PLATFORMS = [Platform.SENSOR]

CODES = vol.Any(cv.string, [cv.string])
TEXT = vol.Any(None, cv.string, vol.Coerce(str))

ADD_SCHEMA = vol.Schema(
    {
        vol.Optional("categorie"): TEXT,
        vol.Optional("naam"): TEXT,
        vol.Optional("hoeveelheid"): TEXT,
        vol.Optional("regel"): TEXT,
        vol.Optional("pakjes", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=50)
        ),
        vol.Optional("ingevroren"): TEXT,
        vol.Optional("op_voor"): TEXT,
        vol.Optional("opmerking"): TEXT,
        vol.Optional("printen", default=False): cv.boolean,
    }
)
SHOPPING_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("uid"): cv.string,
        vol.Optional("categorie"): TEXT,
        vol.Optional("pakjes", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=50)
        ),
        vol.Optional("printen", default=False): cv.boolean,
    }
)
TAKE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): cv.string,
        vol.Optional("hoeveelheid"): TEXT,
        vol.Optional("rest"): TEXT,
    }
)
CODE_SCHEMA = vol.Schema({vol.Required("code"): cv.string})
UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): cv.string,
        vol.Optional("naam"): TEXT,
        vol.Optional("hoeveelheid"): TEXT,
        vol.Optional("ingevroren"): TEXT,
        vol.Optional("op_voor"): TEXT,
        vol.Optional("opmerking"): TEXT,
    }
)
PRINT_SCHEMA = vol.Schema({vol.Optional("codes"): CODES})
RECEIPT_SCHEMA = vol.Schema({vol.Optional("categorie"): TEXT})
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("lijsten"): [
            vol.Schema(
                {
                    vol.Required("entity_id"): cv.entity_id,
                    vol.Required("categorie"): cv.string,
                }
            )
        ]
    }
)

SERVICES = (
    "toevoegen",
    "van_boodschappenlijst",
    "uithalen",
    "terugzetten",
    "wijzigen",
    "verwijderen",
    "labels_printen",
    "uit_wachtrij",
    "bon_printen",
    "importeren",
    "lijst",
)


def _codes(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def _with_response(
    func: Callable[[ServiceCall], Awaitable[dict[str, Any]]],
) -> Callable[[ServiceCall], Awaitable[ServiceResponse]]:
    """With a response requested, report errors as {ok: false, fout} instead of raising."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        if not call.return_response:
            await func(call)
            return None
        try:
            return {"ok": True, **await func(call)}
        except HomeAssistantError as err:
            return {"ok": False, "fout": str(err)}

    return handler


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = Manager(hass, entry)
    await manager.async_load()
    manager.start()
    entry.runtime_data = manager
    hass.data[DOMAIN] = manager
    entry.async_on_unload(entry.add_update_listener(_reload))

    async def add_and_maybe_print(codes: list[str], printen: bool) -> dict[str, Any]:
        result: dict[str, Any] = {"codes": codes}
        if printen:
            printed = await manager.print_labels(codes)
            result.update(printed)
        return result

    async def toevoegen(call: ServiceCall) -> dict[str, Any]:
        d = call.data
        name, amount = d.get("naam"), d.get("hoeveelheid")
        if d.get("regel"):
            name, amount = split_line(d["regel"])
        codes = manager.add(
            d.get("categorie"),
            name,
            amount or "",
            d["pakjes"],
            d.get("ingevroren"),
            d.get("op_voor"),
            d.get("opmerking") or "",
        )
        return await add_and_maybe_print(codes, d["printen"])

    async def van_boodschappenlijst(call: ServiceCall) -> dict[str, Any]:
        """Endpoint for a shopping list: put one item in the freezer and tick it off."""
        d = call.data
        entity_id = d["entity_id"]
        result = await hass.services.async_call(
            "todo",
            "get_items",
            {"status": ["needs_action", "completed"]},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        items = result.get(entity_id, {}).get("items", [])
        item = next((i for i in items if i.get("uid") == d["uid"]), None)
        if item is None:
            raise ServiceValidationError("Die regel staat niet (meer) op de lijst")
        if "naar vriezer" in (item.get("description") or ""):
            raise ServiceValidationError(f"{item['summary']} is al naar de vriezer")
        name, amount = split_line(item["summary"])
        try:
            codes = manager.add(
                d.get("categorie"), name, amount, d["pakjes"], source=entity_id
            )
        except ServiceValidationError:
            # amount not understood: keep the whole line as the name
            codes = manager.add(
                d.get("categorie"), item["summary"], "", d["pakjes"], source=entity_id
            )
        await hass.services.async_call(
            "todo",
            "update_item",
            {
                "item": d["uid"],
                "status": "completed",
                "description": "naar vriezer "
                + dt_util.now().strftime("%d/%m/%Y")
                + " ("
                + ", ".join(codes)
                + ")",
            },
            target={"entity_id": entity_id},
            blocking=True,
        )
        return {**await add_and_maybe_print(codes, d["printen"]), "naam": name}

    async def uithalen(call: ServiceCall) -> dict[str, Any]:
        d = call.data
        return manager.take(d["code"], d.get("hoeveelheid") or "", d.get("rest") or "")

    async def terugzetten(call: ServiceCall) -> dict[str, Any]:
        return {"code": manager.put_back(call.data["code"])["code"]}

    async def wijzigen(call: ServiceCall) -> dict[str, Any]:
        fields = {k: v for k, v in call.data.items() if k != "code"}
        return {"code": manager.update(call.data["code"], fields)["code"]}

    async def verwijderen(call: ServiceCall) -> dict[str, Any]:
        manager.remove_record(call.data["code"])
        return {}

    async def labels_printen(call: ServiceCall) -> dict[str, Any]:
        result = await manager.print_labels(_codes(call.data.get("codes")))
        if result["fout"]:
            raise HomeAssistantError(
                f"{len(result['geprint'])} geprint, daarna fout: {result['fout']}"
            )
        return result

    async def uit_wachtrij(call: ServiceCall) -> dict[str, Any]:
        codes = _codes(call.data.get("codes")) or [
            p["code"] for p in manager.data["pakjes"].values() if not p["geprint"]
        ]
        manager.set_printed(codes)
        return {"codes": codes}

    async def bon_printen(call: ServiceCall) -> dict[str, Any]:
        return {"tekst": await manager.print_receipt(call.data.get("categorie"))}

    async def importeren(call: ServiceCall) -> dict[str, Any]:
        return await manager.import_todo(call.data["lijsten"])

    async def lijst(call: ServiceCall) -> ServiceResponse:
        return manager.snapshot()

    handlers = {
        "toevoegen": (toevoegen, ADD_SCHEMA),
        "van_boodschappenlijst": (van_boodschappenlijst, SHOPPING_SCHEMA),
        "uithalen": (uithalen, TAKE_SCHEMA),
        "terugzetten": (terugzetten, CODE_SCHEMA),
        "wijzigen": (wijzigen, UPDATE_SCHEMA),
        "verwijderen": (verwijderen, CODE_SCHEMA),
        "labels_printen": (labels_printen, PRINT_SCHEMA),
        "uit_wachtrij": (uit_wachtrij, PRINT_SCHEMA),
        "bon_printen": (bon_printen, RECEIPT_SCHEMA),
        "importeren": (importeren, IMPORT_SCHEMA),
    }
    for name, (func, schema) in handlers.items():
        hass.services.async_register(
            DOMAIN, name, _with_response(func), schema, SupportsResponse.OPTIONAL
        )
    hass.services.async_register(
        DOMAIN, "lijst", lijst, supports_response=SupportsResponse.ONLY
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for service in SERVICES:
            hass.services.async_remove(DOMAIN, service)
        await entry.runtime_data.async_stop()
        hass.data.pop(DOMAIN, None)
    return unloaded
