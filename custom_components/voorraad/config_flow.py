"""Config flow (one entry) and options: locations, categories, QR, label and receipt printer."""

from __future__ import annotations

import json
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from homeassistant.util import slugify

from .const import (
    DEFAULT_LOCATIONS,
    DEFAULT_OPTIONS,
    DOMAIN,
    OLD_DOMAIN,
    OPT_CATEGORIES,
    OPT_DASHBOARD,
    OPT_LABEL_ACTION,
    OPT_LABEL_EXTRA,
    OPT_LABEL_FIELD,
    OPT_LABEL_HEIGHT,
    OPT_LABEL_MARGIN_LONG,
    OPT_LABEL_MARGIN_SHORT,
    OPT_LABEL_PAUSE,
    OPT_LABEL_WIDTH,
    OPT_LOCATIONS,
    OPT_NOTIFY,
    OPT_QR,
    OPT_RECEIPT_ACTION,
    OPT_RECEIPT_EXTRA,
    OPT_RECEIPT_FIELD,
    OPT_RECEIPT_WIDTH,
    OPT_SCAN,
    SCAN_MODES,
)

TEXT = sel.TextSelector()
MULTILINE = sel.TextSelector(sel.TextSelectorConfig(multiline=True))
BOOL = sel.BooleanSelector()


def _number(lo: int, hi: int, unit: str | None = None) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(
            min=lo,
            max=hi,
            step=1,
            mode=sel.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


def _check_action(hass, action: str) -> str | None:
    action = (action or "").strip()
    if not action:
        return None
    domain, _, service = action.partition(".")
    return None if hass.services.has_service(domain, service) else "unknown_action"


def _check_json(text: str) -> str | None:
    if not (text or "").strip():
        return None
    try:
        return None if isinstance(json.loads(text), dict) else "bad_json"
    except ValueError:
        return "bad_json"


class VoorraadConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            options = dict(DEFAULT_OPTIONS)
            if self.hass.services.has_service("fichero", "print_image"):
                options[OPT_LABEL_ACTION] = "fichero.print_image"
            old = self.hass.config_entries.async_entries(OLD_DOMAIN)
            if old:
                # take over the settings of the freezer-only version
                prev = dict(old[0].options)
                for cat in prev.get(OPT_CATEGORIES, []):
                    cat.setdefault("locatie", "vriezer")
                    cat.setdefault("label", True)
                prev.pop(OPT_DASHBOARD, None)
                options.update(prev)
                options[OPT_LOCATIONS] = list(DEFAULT_LOCATIONS)
            return self.async_create_entry(
                title="Voorraadbeheer", data={}, options=options
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VoorraadOptionsFlow()


class VoorraadOptionsFlow(OptionsFlow):
    """Menu with one form per subject; every form saves right away."""

    def __init__(self) -> None:
        self._edit: str | None = None

    @property
    def _opts(self) -> dict[str, Any]:
        return {**DEFAULT_OPTIONS, **self.config_entry.options}

    def _save(self, **changes: Any):
        return self.async_create_entry(data={**self._opts, **changes})

    def _cats(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._opts[OPT_CATEGORIES]]

    def _locs(self) -> list[dict[str, Any]]:
        return [dict(x) for x in self._opts[OPT_LOCATIONS]]

    def _loc_name(self, loc_id: str) -> str:
        return next((x["naam"] for x in self._locs() if x["id"] == loc_id), loc_id)

    def _cat_choice(self) -> sel.SelectSelector:
        return sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=[
                    sel.SelectOptionDict(
                        value=c["prefix"],
                        label=f"{c['naam']} ({c['prefix']}, {self._loc_name(c.get('locatie', ''))})",
                    )
                    for c in self._cats()
                ],
                mode=sel.SelectSelectorMode.LIST,
            )
        )

    def _loc_choice(self, mode=sel.SelectSelectorMode.LIST) -> sel.SelectSelector:
        return sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=[
                    sel.SelectOptionDict(
                        value=x["id"],
                        label=x["naam"] + (" (invriezen)" if x["invriezen"] else ""),
                    )
                    for x in self._locs()
                ],
                mode=mode,
            )
        )

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "algemeen",
                "locatie_toevoegen",
                "locatie_kiezen",
                "locatie_verwijderen",
                "categorie_toevoegen",
                "categorie_kiezen",
                "categorie_verwijderen",
                "labelprinter",
                "bonprinter",
            ],
        )

    # ---- general ---------------------------------------------------------

    async def async_step_algemeen(self, user_input=None):
        opts = self._opts
        if user_input is not None:
            return self._save(
                **{
                    OPT_QR: user_input[OPT_QR],
                    OPT_NOTIFY: user_input.get(OPT_NOTIFY, ""),
                    OPT_DASHBOARD: user_input.get(OPT_DASHBOARD, "").strip(),
                    OPT_SCAN: user_input.get(OPT_SCAN, "melding"),
                }
            )
        phones = sorted(
            s
            for s in self.hass.services.async_services().get("notify", {})
            if s.startswith("mobile_app_")
        )
        current = opts[OPT_NOTIFY]
        choices = [
            sel.SelectOptionDict(value="", label="(alleen de telefoon die scant)")
        ] + [
            sel.SelectOptionDict(
                value=p, label=p.removeprefix("mobile_app_").replace("_", " ")
            )
            for p in phones
        ]
        schema = vol.Schema(
            {
                vol.Required(OPT_QR, default=opts[OPT_QR]): BOOL,
                vol.Optional(
                    OPT_NOTIFY, default=current if current in phones else ""
                ): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=choices, mode=sel.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(OPT_DASHBOARD, default=opts[OPT_DASHBOARD]): TEXT,
                vol.Required(OPT_SCAN, default=opts[OPT_SCAN]): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=list(SCAN_MODES),
                        translation_key=OPT_SCAN,
                        mode=sel.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="algemeen", data_schema=schema)

    # ---- locations -------------------------------------------------------

    async def async_step_locatie_toevoegen(self, user_input=None):
        errors: dict[str, str] = {}
        locs = self._locs()
        if user_input is not None:
            name = user_input["naam"].strip()
            loc_id = slugify(name)
            if not name or any(x["id"] == loc_id for x in locs):
                errors["naam"] = "name_taken"
            else:
                locs.append(
                    {"id": loc_id, "naam": name, "invriezen": user_input["invriezen"]}
                )
                return self._save(**{OPT_LOCATIONS: locs})
        schema = vol.Schema(
            {
                vol.Required("naam"): TEXT,
                vol.Required("invriezen", default=False): BOOL,
            }
        )
        return self.async_show_form(
            step_id="locatie_toevoegen",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_locatie_kiezen(self, user_input=None):
        if user_input is not None:
            self._edit = user_input["locatie"]
            return await self.async_step_locatie_wijzigen()
        return self.async_show_form(
            step_id="locatie_kiezen",
            data_schema=vol.Schema({vol.Required("locatie"): self._loc_choice()}),
        )

    async def async_step_locatie_wijzigen(self, user_input=None):
        locs = self._locs()
        loc = next(x for x in locs if x["id"] == self._edit)
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["naam"].strip()
            if not name or any(
                x["naam"].lower() == name.lower() and x is not loc for x in locs
            ):
                errors["naam"] = "name_taken"
            else:
                loc.update(naam=name, invriezen=user_input["invriezen"])
                return self._save(**{OPT_LOCATIONS: locs})
        schema = vol.Schema(
            {
                vol.Required("naam", default=loc["naam"]): TEXT,
                vol.Required("invriezen", default=loc["invriezen"]): BOOL,
            }
        )
        return self.async_show_form(
            step_id="locatie_wijzigen", data_schema=schema, errors=errors
        )

    async def async_step_locatie_verwijderen(self, user_input=None):
        locs = self._locs()
        errors: dict[str, str] = {}
        if user_input is not None:
            loc_id = user_input["locatie"]
            if any(c.get("locatie") == loc_id for c in self._cats()):
                errors["locatie"] = "location_in_use"
            elif len(locs) == 1:
                errors["locatie"] = "last_location"
            else:
                return self._save(
                    **{OPT_LOCATIONS: [x for x in locs if x["id"] != loc_id]}
                )
        return self.async_show_form(
            step_id="locatie_verwijderen",
            data_schema=vol.Schema({vol.Required("locatie"): self._loc_choice()}),
            errors=errors,
        )

    # ---- categories ------------------------------------------------------

    def _cat_schema(self, cat: dict[str, Any], with_prefix: bool) -> vol.Schema:
        fields: dict[Any, Any] = {
            vol.Required("naam", default=cat.get("naam", "")): TEXT
        }
        if with_prefix:
            fields[vol.Required("prefix")] = TEXT
        fields.update(
            {
                vol.Required(
                    "locatie", default=cat.get("locatie") or self._locs()[0]["id"]
                ): self._loc_choice(sel.SelectSelectorMode.DROPDOWN),
                vol.Required("dagen", default=cat.get("dagen", 90)): _number(
                    0, 1000, "dagen"
                ),
                vol.Required("label", default=cat.get("label", True)): BOOL,
                vol.Required("qr", default=cat.get("qr", True)): BOOL,
            }
        )
        return vol.Schema(fields)

    async def async_step_categorie_toevoegen(self, user_input=None):
        errors: dict[str, str] = {}
        cats = self._cats()
        if user_input is not None:
            name = user_input["naam"].strip()
            prefix = user_input["prefix"].strip().upper()
            if not name or any(c["naam"].lower() == name.lower() for c in cats):
                errors["naam"] = "name_taken"
            if not re.fullmatch(r"[A-Z]{1,3}", prefix):
                errors["prefix"] = "bad_prefix"
            elif any(c["prefix"] == prefix for c in cats):
                errors["prefix"] = "prefix_taken"
            if not errors:
                cats.append(
                    {
                        "naam": name,
                        "prefix": prefix,
                        "locatie": user_input["locatie"],
                        "dagen": int(user_input["dagen"]),
                        "label": user_input["label"],
                        "qr": user_input["qr"],
                    }
                )
                return self._save(**{OPT_CATEGORIES: cats})
        return self.async_show_form(
            step_id="categorie_toevoegen",
            data_schema=self.add_suggested_values_to_schema(
                self._cat_schema({}, True), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_categorie_kiezen(self, user_input=None):
        if not self._cats():
            return self.async_abort(reason="no_categories")
        if user_input is not None:
            self._edit = user_input["categorie"]
            return await self.async_step_categorie_wijzigen()
        return self.async_show_form(
            step_id="categorie_kiezen",
            data_schema=vol.Schema({vol.Required("categorie"): self._cat_choice()}),
        )

    async def async_step_categorie_wijzigen(self, user_input=None):
        cats = self._cats()
        cat = next(c for c in cats if c["prefix"] == self._edit)
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["naam"].strip()
            if not name or any(
                c["naam"].lower() == name.lower() and c is not cat for c in cats
            ):
                errors["naam"] = "name_taken"
            else:
                cat.update(
                    naam=name,
                    locatie=user_input["locatie"],
                    dagen=int(user_input["dagen"]),
                    label=user_input["label"],
                    qr=user_input["qr"],
                )
                return self._save(**{OPT_CATEGORIES: cats})
        return self.async_show_form(
            step_id="categorie_wijzigen",
            data_schema=self._cat_schema(cat, False),
            errors=errors,
            description_placeholders={"prefix": cat["prefix"]},
        )

    async def async_step_categorie_verwijderen(self, user_input=None):
        cats = self._cats()
        if not cats:
            return self.async_abort(reason="no_categories")
        errors: dict[str, str] = {}
        if user_input is not None:
            prefix = user_input["categorie"]
            manager = self.hass.data.get(DOMAIN)
            in_use = manager and any(
                p["categorie"] == prefix and not p["uit"]
                for p in manager.data["pakjes"].values()
            )
            if in_use:
                errors["categorie"] = "category_in_use"
            elif len(cats) == 1:
                errors["categorie"] = "last_category"
            else:
                return self._save(
                    **{OPT_CATEGORIES: [c for c in cats if c["prefix"] != prefix]}
                )
        return self.async_show_form(
            step_id="categorie_verwijderen",
            data_schema=vol.Schema({vol.Required("categorie"): self._cat_choice()}),
            errors=errors,
        )

    # ---- printers --------------------------------------------------------

    async def async_step_labelprinter(self, user_input=None):
        opts = self._opts
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := _check_action(self.hass, user_input.get(OPT_LABEL_ACTION, "")):
                errors[OPT_LABEL_ACTION] = err
            if err := _check_json(user_input.get(OPT_LABEL_EXTRA, "")):
                errors[OPT_LABEL_EXTRA] = err
            if not errors:
                return self._save(
                    **{
                        OPT_LABEL_ACTION: user_input.get(OPT_LABEL_ACTION, "").strip(),
                        OPT_LABEL_FIELD: user_input[OPT_LABEL_FIELD].strip(),
                        OPT_LABEL_EXTRA: user_input.get(OPT_LABEL_EXTRA, "").strip(),
                        **{
                            k: int(user_input[k])
                            for k in (
                                OPT_LABEL_WIDTH,
                                OPT_LABEL_HEIGHT,
                                OPT_LABEL_MARGIN_LONG,
                                OPT_LABEL_MARGIN_SHORT,
                                OPT_LABEL_PAUSE,
                            )
                        },
                    }
                )
        schema = vol.Schema(
            {
                vol.Optional(OPT_LABEL_ACTION): TEXT,
                vol.Required(OPT_LABEL_FIELD): TEXT,
                vol.Optional(OPT_LABEL_EXTRA): MULTILINE,
                vol.Required(OPT_LABEL_WIDTH): _number(50, 2000, "px"),
                vol.Required(OPT_LABEL_HEIGHT): _number(30, 2000, "px"),
                vol.Required(OPT_LABEL_MARGIN_LONG): _number(0, 200, "px"),
                vol.Required(OPT_LABEL_MARGIN_SHORT): _number(0, 200, "px"),
                vol.Required(OPT_LABEL_PAUSE): _number(0, 120, "s"),
            }
        )
        return self.async_show_form(
            step_id="labelprinter",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or opts),
            errors=errors,
        )

    async def async_step_bonprinter(self, user_input=None):
        opts = self._opts
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := _check_action(self.hass, user_input.get(OPT_RECEIPT_ACTION, "")):
                errors[OPT_RECEIPT_ACTION] = err
            if err := _check_json(user_input.get(OPT_RECEIPT_EXTRA, "")):
                errors[OPT_RECEIPT_EXTRA] = err
            if not errors:
                return self._save(
                    **{
                        OPT_RECEIPT_ACTION: user_input.get(
                            OPT_RECEIPT_ACTION, ""
                        ).strip(),
                        OPT_RECEIPT_FIELD: user_input[OPT_RECEIPT_FIELD].strip(),
                        OPT_RECEIPT_EXTRA: user_input.get(
                            OPT_RECEIPT_EXTRA, ""
                        ).strip(),
                        OPT_RECEIPT_WIDTH: int(user_input[OPT_RECEIPT_WIDTH]),
                    }
                )
        schema = vol.Schema(
            {
                vol.Optional(OPT_RECEIPT_ACTION): TEXT,
                vol.Required(OPT_RECEIPT_FIELD): TEXT,
                vol.Required(OPT_RECEIPT_WIDTH): _number(24, 80, "tekens"),
                vol.Optional(OPT_RECEIPT_EXTRA): MULTILINE,
            }
        )
        return self.async_show_form(
            step_id="bonprinter",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or opts),
            errors=errors,
        )
