"""Inventory: packages per location with a code, label queue, partial removal, QR scans."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import label
from .const import (
    ACTION_ALL,
    ACTION_REPLY,
    ACTION_UNDO,
    DEFAULT_OPTIONS,
    DOMAIN,
    HISTORY_DAYS,
    LABEL_DIR,
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
    SCAN_MEMORY_MINUTES,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TAG_PREFIX,
    TAG_URL,
)

_LOGGER = logging.getLogger(__name__)

# unit -> (base unit, factor to base)
UNITS = {
    "g": ("g", 1),
    "gr": ("g", 1),
    "gram": ("g", 1),
    "kg": ("g", 1000),
    "kilo": ("g", 1000),
    "ons": ("g", 100),
    "ml": ("ml", 1),
    "cl": ("ml", 10),
    "dl": ("ml", 100),
    "l": ("ml", 1000),
    "liter": ("ml", 1000),
    "st": ("st", 1),
    "stuk": ("st", 1),
    "stuks": ("st", 1),
    "x": ("st", 1),
    "plak": ("st", 1),
    "plakken": ("st", 1),
    "bakje": ("st", 1),
    "bakjes": ("st", 1),
    "pak": ("st", 1),
    "pakken": ("st", 1),
    "blik": ("st", 1),
    "blikken": ("st", 1),
    "pot": ("st", 1),
    "potten": ("st", 1),
    "fles": ("st", 1),
    "flessen": ("st", 1),
    "portie": ("st", 1),
    "porties": ("st", 1),
}
UNIT_RE = "|".join(sorted(UNITS, key=len, reverse=True))
AMOUNT_RE = re.compile(rf"^(\d+(?:[.,]\d+)?)\s*({UNIT_RE})?\.?$", re.IGNORECASE)
TRAILING_RE = re.compile(
    rf"^(.*?)\s+(\d+(?:[.,]\d+)?\s*(?:{UNIT_RE})?)\.?$", re.IGNORECASE
)
CODE_RE = re.compile(r"^([A-Z]{1,3})-?([0-9A-F]{1,6})$")


# ---- parsing helpers ---------------------------------------------------------


def parse_amount(text: Any) -> tuple[float | None, str]:
    """'200 g' -> (200, 'g'); '1,5kg' -> (1.5, 'kg'); '' -> (None, ''); 'een bak' -> error."""
    text = str(text or "").strip().replace("½", "0.5")
    if not text:
        return None, ""
    match = AMOUNT_RE.match(text)
    if not match:
        raise ServiceValidationError(
            f"Hoeveelheid '{text}' snap ik niet, gebruik bv. 200 g, 1,5 kg of 3 stuks"
        )
    unit = (match.group(2) or "").lower()
    return float(match.group(1).replace(",", ".")), unit


def split_line(text: str) -> tuple[str, str]:
    """Shopping/todo line -> (name, amount text): 'Gehakt - 200 g' or 'Gehakt 200g'."""
    text = str(text or "").strip()
    if " - " in text:
        name, amount = text.split(" - ", 1)
        return name.strip(), amount.strip()
    match = TRAILING_RE.match(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, ""


def parse_date(value: Any, label_text: str) -> str | None:
    """Accept yyyy-mm-dd or dd/mm/yyyy; empty means no date."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    raise ServiceValidationError(f"Ongeldige {label_text} '{text}', gebruik dd/mm/jjjj")


def normalize_code(value: Any) -> str:
    """'v0002', 'V 2', 'V-0002' -> 'V-0002'."""
    text = str(value or "").strip().upper().replace(" ", "")
    match = CODE_RE.match(text)
    if not match:
        return text
    return f"{match.group(1)}-{int(match.group(2), 16):04X}"


def fmt_amount(value: float | None, unit: str) -> str:
    if value is None:
        return ""
    number = f"{round(value, 2):g}".replace(".", ",")
    return f"{number} {unit}".strip()


def nl_date(iso: str | None) -> str:
    return "/".join(reversed(iso.split("-"))) if iso else ""


def full_name(pkg: dict[str, Any]) -> str:
    return f"{pkg['product']} {pkg.get('omschrijving') or ''}".strip()


# ---- manager -------------------------------------------------------------------


class Manager:
    """Owns the stored packages and all actions on them."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options: dict[str, Any] = {**DEFAULT_OPTIONS, **entry.options}
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {"teller": 0, "pakjes": {}, "producten": {}}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_scan: tuple[str, datetime] | None = None

    # ---- lifecycle -------------------------------------------------------

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self.data = stored
        else:
            await self._take_over_freezer()
        self.data.setdefault("teller", 0)
        self.data.setdefault("pakjes", {})
        self.data.setdefault("producten", {})
        self._purge()

    async def _take_over_freezer(self) -> None:
        """First start: take over the data of the freezer-only version (.storage/vriezer)."""
        old = await Store(self.hass, 1, OLD_DOMAIN).async_load()
        if not old or not old.get("pakjes"):
            return
        self.data = {"teller": old.get("teller", 0), "pakjes": {}, "producten": {}}
        for code, pkg in old["pakjes"].items():
            new = {
                "code": code,
                "categorie": pkg["categorie"],
                "product": pkg["naam"],
                "omschrijving": pkg.get("opmerking", ""),
                "hoeveelheid": pkg.get("hoeveelheid"),
                "eenheid": pkg.get("eenheid", ""),
                "begin": pkg.get("begin"),
                "ingevroren": pkg.get("ingevroren"),
                "op_voor": pkg.get("op_voor"),
                "toegevoegd": pkg.get("toegevoegd"),
                "geprint": pkg.get("geprint", True),
                "uit": pkg.get("uit"),
                "log": pkg.get("log", []),
            }
            self.data["pakjes"][code] = new
            self._remember_product(new)
        _LOGGER.info(
            "Took over %s packages from the vriezer integration", len(old["pakjes"])
        )
        await self._store.async_save(self.data)

    @callback
    def start(self) -> None:
        bus = self.hass.bus
        self._unsubs.append(bus.async_listen("tag_scanned", self._on_tag))
        self._unsubs.append(
            bus.async_listen("mobile_app_notification_action", self._on_action)
        )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self.data)

    def _purge(self) -> None:
        cutoff = (dt_util.now() - timedelta(days=HISTORY_DAYS)).isoformat()
        for code in [
            c
            for c, p in self.data["pakjes"].items()
            if p.get("uit") and p["uit"] < cutoff
        ]:
            del self.data["pakjes"][code]

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(lambda: self.data, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # ---- locations and categories ----------------------------------------

    @property
    def locations(self) -> list[dict[str, Any]]:
        return self.options[OPT_LOCATIONS]

    @property
    def categories(self) -> list[dict[str, Any]]:
        return self.options[OPT_CATEGORIES]

    def category(self, value: Any) -> dict[str, Any]:
        """Find a category by name or prefix (case insensitive); empty = the first one."""
        if not self.categories:
            raise ServiceValidationError(
                "Maak eerst een categorie aan in de instellingen"
            )
        text = str(value or "").strip().lower()
        if not text:
            return self.categories[0]
        for cat in self.categories:
            if text in (cat["naam"].lower(), cat["prefix"].lower()):
                return cat
        names = ", ".join(c["naam"] for c in self.categories)
        raise ServiceValidationError(f"Categorie '{value}' bestaat niet (wel: {names})")

    def _cat_by_prefix(self, prefix: str) -> dict[str, Any] | None:
        return next((c for c in self.categories if c["prefix"] == prefix), None)

    def location_of(self, cat: dict[str, Any] | None) -> dict[str, Any]:
        loc_id = (cat or {}).get("locatie")
        for loc in self.locations:
            if loc["id"] == loc_id:
                return loc
        return {"id": loc_id or "", "naam": loc_id or "Onbekend", "invriezen": False}

    def _pkg_location(self, pkg: dict[str, Any]) -> dict[str, Any]:
        return self.location_of(self._cat_by_prefix(pkg["categorie"]))

    # ---- packages --------------------------------------------------------

    def get(self, code: Any) -> dict[str, Any]:
        norm = normalize_code(code)
        pkg = self.data["pakjes"].get(norm)
        if pkg is None:
            raise ServiceValidationError(f"Code {norm or code} niet gevonden")
        return pkg

    def _new_code(self, prefix: str) -> str:
        while True:
            self.data["teller"] += 1
            code = f"{prefix}-{self.data['teller']:04X}"
            if code not in self.data["pakjes"]:
                return code

    @staticmethod
    def _log(pkg: dict[str, Any], text: str) -> None:
        pkg.setdefault("log", []).append(
            {"t": dt_util.now().isoformat(timespec="seconds"), "tekst": text}
        )

    def _remember_product(self, pkg: dict[str, Any]) -> None:
        key = pkg["product"].strip().lower()
        if key:
            self.data["producten"][key] = {
                "naam": pkg["product"].strip(),
                "categorie": pkg["categorie"],
                "eenheid": pkg.get("eenheid", ""),
            }

    def _dates(
        self, cat: dict[str, Any], frozen: Any, best_before: Any
    ) -> tuple[str | None, str]:
        """(freeze date, use-before date) for a new package in this category."""
        loc = self.location_of(cat)
        if loc["invriezen"]:
            frozen_iso = (
                parse_date(frozen, "invriesdatum") or dt_util.now().date().isoformat()
            )
            before = (
                parse_date(best_before, "houdbaarheidsdatum")
                or (
                    date.fromisoformat(frozen_iso)
                    + timedelta(days=int(cat.get("dagen") or 0))
                ).isoformat()
            )
            return frozen_iso, before
        before = parse_date(best_before, "houdbaarheidsdatum")
        if not before:
            raise ServiceValidationError(
                f"Vul de houdbaarheidsdatum in ({cat['naam']} staat in de {loc['naam'].lower()})"
            )
        return None, before

    def add(
        self,
        category: Any,
        product: str,
        description: str = "",
        amount: Any = "",
        packages: int = 1,
        frozen: Any = None,
        best_before: Any = None,
        source: str = "",
    ) -> list[str]:
        cat = self.category(category)
        product = str(product or "").strip()
        if not product:
            raise ServiceValidationError("Vul een product in")
        known = self.data["producten"].get(product.lower())
        if known:
            product = known["naam"]  # same spelling as before
        qty, unit = parse_amount(amount)
        frozen_iso, before = self._dates(cat, frozen, best_before)
        now = dt_util.now().isoformat(timespec="seconds")
        codes = []
        for _ in range(max(1, min(int(packages or 1), 50))):
            code = self._new_code(cat["prefix"])
            pkg = {
                "code": code,
                "categorie": cat["prefix"],
                "product": product,
                "omschrijving": str(description or "").strip(),
                "hoeveelheid": qty,
                "eenheid": unit,
                "begin": qty,
                "ingevroren": frozen_iso,
                "op_voor": before,
                "toegevoegd": now,
                "geprint": not cat.get(
                    "label", True
                ),  # no label needed = nothing to print
                "uit": None,
                "log": [],
            }
            self._log(pkg, f"toegevoegd{' via ' + source if source else ''}")
            self.data["pakjes"][code] = pkg
            codes.append(code)
        self._remember_product(self.data["pakjes"][codes[0]])
        self._changed()
        return codes

    def update(self, code: Any, fields: dict[str, Any]) -> dict[str, Any]:
        pkg = self.get(code)
        new: dict[str, Any] = {}
        if fields.get("product") is not None:
            new["product"] = str(fields["product"]).strip()
            if not new["product"]:
                raise ServiceValidationError("Product mag niet leeg zijn")
        if fields.get("omschrijving") is not None:
            new["omschrijving"] = str(fields["omschrijving"]).strip()
        if fields.get("hoeveelheid") is not None:
            new["hoeveelheid"], new["eenheid"] = parse_amount(fields["hoeveelheid"])
        if fields.get("categorie"):
            new["categorie"] = self.category(fields["categorie"])["prefix"]
        if fields.get("ingevroren"):
            new["ingevroren"] = parse_date(fields["ingevroren"], "invriesdatum")
        if fields.get("houdbaar_tot"):
            new["op_voor"] = parse_date(fields["houdbaar_tot"], "houdbaarheidsdatum")
        changes = []
        for key, value in new.items():
            if pkg.get(key) != value:
                pkg[key] = value
                if key != "eenheid":
                    changes.append(key)
        if changes:
            self._log(pkg, "gewijzigd: " + ", ".join(changes))
            self._remember_product(pkg)
        self._changed()
        return pkg

    def remove_record(self, code: Any) -> None:
        """Delete a package completely (a mistake), no history."""
        pkg = self.get(code)
        del self.data["pakjes"][pkg["code"]]
        self._changed()

    def forget_product(self, name: Any) -> None:
        if self.data["producten"].pop(str(name or "").strip().lower(), None) is None:
            raise ServiceValidationError(f"Product '{name}' staat niet in de lijst")
        self._changed()

    def take(self, code: Any, amount: Any = "", rest: Any = "") -> dict[str, Any]:
        """Take a package out: all of it, an amount, or down to a rest."""
        pkg = self.get(code)
        if pkg.get("uit"):
            raise ServiceValidationError(f"{pkg['code']} {full_name(pkg)} is al weg")
        amount, rest = str(amount or "").strip(), str(rest or "").strip()
        if not amount and not rest:
            return self._take_all(pkg)
        have = pkg.get("hoeveelheid")
        if have is None:
            raise ServiceValidationError(
                f"Van {full_name(pkg)} is geen hoeveelheid bekend; haal het helemaal eruit "
                "of vul eerst een hoeveelheid in (Wijzigen)"
            )
        value, unit = parse_amount(rest or amount)
        value = self._convert(value, unit, pkg["eenheid"])
        left = value if rest else have - value
        if left <= 1e-9:
            return self._take_all(pkg)
        if left >= have - 1e-9:
            raise ServiceValidationError(
                f"Er is nu {fmt_amount(have, pkg['eenheid'])}; dat blijft dan gelijk"
            )
        pkg["hoeveelheid"] = round(left, 3)
        self._log(
            pkg,
            f"deel eruit: {fmt_amount(have - left, pkg['eenheid'])}, "
            f"over {fmt_amount(left, pkg['eenheid'])}",
        )
        self._changed()
        return {
            "code": pkg["code"],
            "over": fmt_amount(left, pkg["eenheid"]),
            "op": False,
        }

    @staticmethod
    def _convert(value: float, unit: str, target: str) -> float:
        if not unit or unit == target or not target:
            return value
        a, b = UNITS.get(unit), UNITS.get(target)
        if not a or not b or a[0] != b[0]:
            raise ServiceValidationError(f"Kan {unit} niet omrekenen naar {target}")
        return value * a[1] / b[1]

    def _take_all(self, pkg: dict[str, Any]) -> dict[str, Any]:
        pkg["uit"] = dt_util.now().isoformat(timespec="seconds")
        self._log(pkg, "helemaal eruit")
        self._changed()
        return {"code": pkg["code"], "over": "", "op": True}

    def put_back(self, code: Any) -> dict[str, Any]:
        pkg = self.get(code)
        if not pkg.get("uit"):
            raise ServiceValidationError(f"{pkg['code']} is er nog")
        pkg["uit"] = None
        self._log(pkg, "teruggezet")
        self._changed()
        return pkg

    def set_printed(self, codes: list[str], printed: bool = True) -> None:
        for code in codes:
            self.get(code)["geprint"] = printed
        self._changed()

    # ---- import ----------------------------------------------------------

    async def import_todo(self, lists: list[dict[str, Any]]) -> dict[str, Any]:
        """Take over packages from todo lists.

        Item 'Product - hoeveelheid', description 'V-0001 | ingevroren dd/mm/jjjj',
        due date = use before, completed = taken out.
        """
        entity_ids = [item["entity_id"] for item in lists]
        result = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"status": ["needs_action", "completed"]},
            target={"entity_id": entity_ids},
            blocking=True,
            return_response=True,
        )
        added, skipped = [], []
        highest = self.data["teller"]
        for item_list in lists:
            cat = self.category(item_list["categorie"])
            freezer = self.location_of(cat)["invriezen"]
            for item in result.get(item_list["entity_id"], {}).get("items", []):
                desc = item.get("description") or ""
                code = normalize_code(desc.split("|")[0])
                if not CODE_RE.match(code) or code in self.data["pakjes"]:
                    skipped.append(item["summary"])
                    continue
                name, amount = split_line(item["summary"])
                try:
                    qty, unit = parse_amount(amount)
                    note = ""
                except ServiceValidationError:
                    qty, unit, note = None, "", amount
                frozen = None
                if "ingevroren" in desc:
                    frozen = parse_date(desc.split("ingevroren", 1)[1].strip(), "datum")
                if freezer:
                    frozen = frozen or dt_util.now().date().isoformat()
                before = item.get("due") or (
                    (
                        date.fromisoformat(frozen)
                        + timedelta(days=int(cat.get("dagen") or 0))
                    ).isoformat()
                    if frozen
                    else None
                )
                if not before:
                    skipped.append(item["summary"])
                    continue
                done = item.get("status") == "completed"
                pkg = {
                    "code": code,
                    "categorie": code.split("-")[0],
                    "product": name,
                    "omschrijving": note,
                    "hoeveelheid": qty,
                    "eenheid": unit,
                    "begin": qty,
                    "ingevroren": frozen,
                    "op_voor": before[:10],
                    "toegevoegd": dt_util.now().isoformat(timespec="seconds"),
                    "geprint": True,
                    "uit": (item.get("completed") or dt_util.now().isoformat())
                    if done
                    else None,
                    "log": [],
                }
                self._log(pkg, f"overgenomen uit {item_list['entity_id']}")
                self.data["pakjes"][code] = pkg
                self._remember_product(pkg)
                highest = max(highest, int(code.split("-")[1], 16))
                added.append(code)
        self.data["teller"] = highest
        self._changed()
        return {"overgenomen": added, "overgeslagen": skipped, "teller": highest}

    # ---- labels ----------------------------------------------------------

    def tag_url(self, code: str) -> str:
        return f"{TAG_URL}{TAG_PREFIX}{code}"

    def _wants_qr(self, pkg: dict[str, Any]) -> bool:
        cat = self._cat_by_prefix(pkg["categorie"])
        return bool(self.options[OPT_QR] and (cat is None or cat.get("qr", True)))

    async def print_labels(self, codes: list[str] | None = None) -> dict[str, Any]:
        """Print the given codes, or the whole queue (not yet printed, still there)."""
        if codes:
            pkgs = [self.get(c) for c in codes]
        else:
            pkgs = [
                p
                for p in self.data["pakjes"].values()
                if not p["geprint"] and not p["uit"]
            ]
        if not pkgs:
            raise ServiceValidationError("Er staan geen labels klaar om te printen")
        printed, error = [], ""
        pause = max(0.0, float(self.options[OPT_LABEL_PAUSE] or 0))
        for index, pkg in enumerate(pkgs):
            if index and pause:
                await asyncio.sleep(pause)
            try:
                await self._print_one(pkg)
            except HomeAssistantError as err:
                error = str(err)
                break
            pkg["geprint"] = True
            printed.append(pkg["code"])
        if printed:
            self._changed()
        return {"geprint": printed, "fout": error}

    def _label_date(self, pkg: dict[str, Any]) -> str:
        if pkg.get("ingevroren"):
            return nl_date(pkg["ingevroren"])
        return f"THT {nl_date(pkg['op_voor'])}"

    async def _print_one(self, pkg: dict[str, Any]) -> None:
        opt = self.options
        qr = self.tag_url(pkg["code"]) if self._wants_qr(pkg) else None
        path = Path(self.hass.config.path(LABEL_DIR, f"{pkg['code']}.png"))
        img = await self.hass.async_add_executor_job(
            label.render,
            int(opt[OPT_LABEL_WIDTH]),
            int(opt[OPT_LABEL_HEIGHT]),
            int(opt[OPT_LABEL_MARGIN_LONG]),
            int(opt[OPT_LABEL_MARGIN_SHORT]),
            pkg["code"],
            full_name(pkg),
            self._label_date(pkg),
            qr,
        )
        await self.hass.async_add_executor_job(label.save, img, path)
        event = {
            "code": pkg["code"],
            "product": pkg["product"],
            "omschrijving": pkg.get("omschrijving", ""),
            "hoeveelheid": fmt_amount(pkg["hoeveelheid"], pkg["eenheid"]),
            "ingevroren": nl_date(pkg.get("ingevroren")),
            "houdbaar_tot": nl_date(pkg["op_voor"]),
            "locatie": self._pkg_location(pkg)["naam"],
            "bestand": str(path),
            "qr": qr or "",
        }
        self.hass.bus.async_fire(f"{DOMAIN}_label", event)
        action = str(opt[OPT_LABEL_ACTION] or "").strip()
        if not action:
            return  # only the event: an own automation prints it
        data = {**self._extra(opt[OPT_LABEL_EXTRA]), opt[OPT_LABEL_FIELD]: str(path)}
        await self._call(action, data, "Label printen")

    @staticmethod
    def _extra(text: Any) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError as err:
            raise HomeAssistantError(
                f"Extra gegevens zijn geen geldige JSON: {err}"
            ) from err
        if not isinstance(value, dict):
            raise HomeAssistantError("Extra gegevens moeten een JSON-object zijn")
        return value

    async def _call(self, action: str, data: dict[str, Any], what: str) -> None:
        domain, _, service = action.removeprefix("action:").strip().partition(".")
        if not self.hass.services.has_service(domain, service):
            raise HomeAssistantError(f"{what}: actie {action} bestaat niet")
        wants = self.hass.services.supports_response(domain, service)
        try:
            response = await self.hass.services.async_call(
                domain,
                service,
                data,
                blocking=True,
                return_response=wants != "none",
            )
        except HomeAssistantError as err:
            raise HomeAssistantError(f"{what} mislukt: {err}") from err
        except Exception as err:  # printer integrations raise their own types
            _LOGGER.exception("%s via %s failed", what, action)
            raise HomeAssistantError(f"{what} mislukt: {err}") from err
        if isinstance(response, dict) and response.get("ok") is False:
            raise HomeAssistantError(f"{what} mislukt: {response.get('fout', '')}")

    # ---- receipt ---------------------------------------------------------

    def receipt_text(self, location: Any = None) -> str:
        width = max(24, int(self.options[OPT_RECEIPT_WIDTH]))
        today = dt_util.now().date().isoformat()
        locs = self.locations
        if location:
            text = str(location).strip().lower()
            locs = [x for x in locs if text in (x["id"], x["naam"].lower())]
            if not locs:
                raise ServiceValidationError(f"Locatie '{location}' bestaat niet")
        lines = ["VOORRAAD", dt_util.now().strftime("%d/%m/%Y %H:%M"), "=" * width]

        def two(left: str, right: str) -> str:
            space = width - len(right) - 1
            return f"{left[:space]:<{space}} {right}"

        for loc in locs:
            pkgs = sorted(
                (
                    p
                    for p in self.data["pakjes"].values()
                    if not p["uit"] and self._pkg_location(p)["id"] == loc["id"]
                ),
                key=lambda p: (p["product"].lower(), p["op_voor"] or ""),
            )
            lines += ["", two(loc["naam"].upper(), f"{len(pkgs)} stuks"), "-" * width]
            if not pkgs:
                lines.append("(leeg)")
            groups: dict[str, list[dict[str, Any]]] = {}
            for pkg in pkgs:
                groups.setdefault(pkg["product"].lower(), []).append(pkg)
            for group in groups.values():
                total = self._total(group)
                lines.append(two(group[0]["product"], f"{len(group)}x {total}".strip()))
                for pkg in group:
                    late = "!" if pkg["op_voor"] and pkg["op_voor"] < today else " "
                    amount = fmt_amount(pkg["hoeveelheid"], pkg["eenheid"])
                    left = f" {pkg['code']} {pkg.get('omschrijving') or ''} {amount}"
                    when = nl_date(pkg["op_voor"])
                    when = when[:6] + when[8:] if when else ""
                    lines.append(two(" " + " ".join(left.split()), f"{late}{when}"))
        lines += ["", "! = over de datum", ""]
        return "\n".join(lines)

    @staticmethod
    def _total(group: list[dict[str, Any]]) -> str:
        sums: dict[str, float] = {}
        for pkg in group:
            if pkg["hoeveelheid"] is not None:
                sums[pkg["eenheid"]] = sums.get(pkg["eenheid"], 0) + pkg["hoeveelheid"]
        return " + ".join(fmt_amount(v, u) for u, v in sums.items())

    async def print_receipt(self, location: Any = None) -> str:
        text = self.receipt_text(location)
        action = str(self.options[OPT_RECEIPT_ACTION] or "").strip()
        self.hass.bus.async_fire(f"{DOMAIN}_bon", {"tekst": text})
        if action:
            data = {**self._extra(self.options[OPT_RECEIPT_EXTRA])}
            data[self.options[OPT_RECEIPT_FIELD]] = text
            await self._call(action, data, "Bon printen")
        return text

    # ---- snapshot for the sensor -----------------------------------------

    def snapshot(self) -> dict[str, Any]:
        today = dt_util.now().date().isoformat()
        cats = {c["prefix"]: c for c in self.categories}

        def view(pkg: dict[str, Any]) -> dict[str, Any]:
            cat = cats.get(pkg["categorie"])
            loc = self.location_of(cat)
            return {
                "code": pkg["code"],
                "categorie": cat["naam"] if cat else pkg["categorie"],
                "locatie": loc["naam"],
                "locatie_id": loc["id"],
                "invriezen": loc["invriezen"],
                "label": bool(cat.get("label", True)) if cat else True,
                "product": pkg["product"],
                "omschrijving": pkg.get("omschrijving", ""),
                "hoeveelheid": fmt_amount(pkg["hoeveelheid"], pkg["eenheid"]),
                "getal": pkg["hoeveelheid"],
                "eenheid": pkg["eenheid"],
                "begin": fmt_amount(pkg.get("begin"), pkg["eenheid"]),
                "ingevroren": pkg.get("ingevroren"),
                "op_voor": pkg["op_voor"],
                "over_datum": bool(pkg["op_voor"] and pkg["op_voor"] < today),
                "toegevoegd": (pkg.get("toegevoegd") or "")[:10],
                "geprint": pkg["geprint"],
                "uit": pkg["uit"],
            }

        active = sorted(
            (p for p in self.data["pakjes"].values() if not p["uit"]),
            key=lambda p: (p["product"].lower(), p["op_voor"] or ""),
        )
        gone = sorted(
            (p for p in self.data["pakjes"].values() if p["uit"]),
            key=lambda p: p["uit"],
            reverse=True,
        )[:20]
        return {
            "pakjes": [view(p) for p in active],
            "recent_uit": [view(p) for p in gone],
            "locaties": [
                {"id": x["id"], "naam": x["naam"], "invriezen": x["invriezen"]}
                for x in self.locations
            ],
            "categorieen": [
                {
                    "naam": c["naam"],
                    "prefix": c["prefix"],
                    "locatie": self.location_of(c)["naam"],
                    "invriezen": self.location_of(c)["invriezen"],
                    "label": bool(c.get("label", True)),
                }
                for c in self.categories
            ],
            "producten": sorted(
                (
                    {
                        "naam": p["naam"],
                        "categorie": cats[p["categorie"]]["naam"]
                        if p["categorie"] in cats
                        else "",
                    }
                    for p in self.data["producten"].values()
                ),
                key=lambda p: p["naam"].lower(),
            ),
            "te_printen": sum(1 for p in active if not p["geprint"]),
            "qr": bool(self.options[OPT_QR]),
            "labelprinter": bool(str(self.options[OPT_LABEL_ACTION] or "").strip()),
            "bonprinter": bool(str(self.options[OPT_RECEIPT_ACTION] or "").strip()),
        }

    # ---- QR scans and notification actions -------------------------------

    def _notify_service(self, device_id: str | None) -> str | None:
        """notify service of the phone that scanned, else the one from the options."""
        return self._phone(device_id)[0]

    def _phone(self, device_id: str | None) -> tuple[str | None, str]:
        """(notify service, os name) of the phone that scanned, else from the options."""
        if device_id:
            device = dr.async_get(self.hass).async_get(device_id)
            entry_ids = []
            if device is not None:
                primary = getattr(device, "config_entry_id", None)
                entry_ids = [primary] if primary else list(device.config_entries)
            for entry_id in entry_ids:
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry and entry.domain == "mobile_app":
                    name = entry.data.get("device_name") or device.name or ""
                    service = f"mobile_app_{slugify(name)}"
                    if self.hass.services.has_service("notify", service):
                        return service, str(entry.data.get("os_name") or "")
        fallback = str(self.options[OPT_NOTIFY] or "").removeprefix("notify.").strip()
        if fallback and self.hass.services.has_service("notify", fallback):
            return fallback, ""
        return None, ""

    def _page(self, code: str) -> str:
        dashboard = str(self.options[OPT_DASHBOARD] or "").strip()
        return f"{dashboard}?voorraad={code}" if dashboard else ""

    async def _on_tag(self, event: Event) -> None:
        tag_id = str(event.data.get("tag_id") or "")
        if not tag_id.startswith(TAG_PREFIX):
            return
        code = normalize_code(tag_id[len(TAG_PREFIX) :])
        pkg = self.data["pakjes"].get(code)
        service, os_name = self._phone(event.data.get("device_id"))
        if service is None:
            _LOGGER.warning("Scan of %s, but no phone to notify", code)
            return
        self._last_scan = (code, dt_util.now())
        if pkg is None:
            await self._send(service, code, "Voorraad", f"Code {code} ken ik niet.", [])
            return
        mode = self.options[OPT_SCAN]
        page = self._page(code)
        # Opening a page from a notification command only exists in the Android app
        can_open = bool(page) and os_name.lower() != "ios"
        if can_open and mode in ("openen", "beide"):
            await self._call_notify(
                service, {"message": "command_webview", "data": {"command": page}}
            )
            if mode == "openen":
                return
        await self._send_package(service, pkg)

    async def _send_package(
        self, service: str, pkg: dict[str, Any], intro: str = ""
    ) -> None:
        amount = fmt_amount(pkg["hoeveelheid"], pkg["eenheid"])
        page = self._page(pkg["code"])
        open_btn = [{"action": "URI", "title": "Openen", "uri": page}] if page else []
        loc = self._pkg_location(pkg)
        if pkg["uit"]:
            when = dt_util.parse_datetime(pkg["uit"])
            msg = f"{intro}Is op {when.strftime('%d/%m') if when else '?'} uit de {loc['naam'].lower()} gehaald."
            actions = [
                {"action": f"{ACTION_UNDO}|{pkg['code']}", "title": "Terugzetten"}
            ]
        else:
            dates = (
                f"ingevroren {nl_date(pkg['ingevroren'])}, op voor {nl_date(pkg['op_voor'])}"
                if pkg.get("ingevroren")
                else f"houdbaar tot {nl_date(pkg['op_voor'])}"
            )
            msg = f"{intro}{amount + ', ' if amount else ''}{loc['naam']}, {dates}"
            actions = [
                {"action": f"{ACTION_ALL}|{pkg['code']}", "title": "Helemaal eruit"}
            ]
            if pkg["hoeveelheid"] is not None:
                actions.append({"action": ACTION_REPLY, "title": "Deel eruit"})
        title = f"{full_name(pkg)} ({pkg['code']})"
        await self._send(service, pkg["code"], title, msg, actions + open_btn)

    async def _send(
        self, service: str, code: str, title: str, message: str, actions: list
    ) -> None:
        data = {"tag": f"voorraad-{code}", "channel": "Voorraad", "actions": actions}
        if page := self._page(code):
            # tapping the notification itself opens the package (Android / iOS)
            data["clickAction"] = page
            data["url"] = page
        await self._call_notify(
            service, {"title": title, "message": message, "data": data}
        )

    async def _call_notify(self, service: str, payload: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(
                "notify", service, payload, blocking=True
            )
        except Exception:
            _LOGGER.exception(
                "Sending inventory notification via notify.%s failed", service
            )

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if action.startswith((ACTION_ALL + "|", ACTION_UNDO + "|")):
            kind, code = action.split("|", 1)
        elif action == ACTION_REPLY:
            tag = str(event.data.get("tag") or "")
            code = tag.removeprefix("voorraad-") if tag.startswith("voorraad-") else ""
            if not code and self._last_scan:
                last, when = self._last_scan
                if dt_util.now() - when < timedelta(minutes=SCAN_MEMORY_MINUTES):
                    code = last
            if not code:
                return
            kind = ACTION_REPLY
        else:
            return
        service = self._notify_service(event.data.get("device_id")) or str(
            self.options[OPT_NOTIFY] or ""
        ).removeprefix("notify.")
        pkg = self.data["pakjes"].get(code)
        if pkg is None:
            return
        try:
            if kind == ACTION_ALL:
                self.take(code)
                intro = "Helemaal eruit. "
            elif kind == ACTION_UNDO:
                self.put_back(code)
                intro = "Teruggezet. "
            else:
                reply = str(event.data.get("reply_text") or "").strip()
                low = reply.lower()
                if low.startswith(("rest", "over", "nog")):
                    result = self.take(code, rest=reply.split(None, 1)[-1])
                else:
                    result = self.take(code, amount=reply)
                intro = (
                    "Helemaal op. " if result["op"] else f"Nog {result['over']} over. "
                )
        except HomeAssistantError as err:
            intro = f"Niet gelukt: {err}. "
        if service:
            await self._send_package(service, pkg, intro)
