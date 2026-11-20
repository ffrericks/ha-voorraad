"""Constants for the inventory integration (Voorraadbeheer)."""

DOMAIN = "voorraad"
STORAGE_KEY = "voorraad"
STORAGE_VERSION = 1
OLD_DOMAIN = "vriezer"  # the integration this one replaces; data is taken over once
SIGNAL_UPDATED = "voorraad_updated"

# Removed packages stay in storage this long (history, reprint, undo)
HISTORY_DAYS = 365
# A notification reply ("Deel eruit") belongs to the last scan within this window
SCAN_MEMORY_MINUTES = 15

TAG_PREFIX = "vz"  # tag id = "vz" + code, e.g. vzV-0004 (kept from the freezer version)
TAG_URL = "https://www.home-assistant.io/tag/"

ACTION_ALL = "VOORRAAD_OP"  # notification action "VOORRAAD_OP|V-0004"
ACTION_UNDO = "VOORRAAD_TERUG"
ACTION_REPLY = "REPLY"  # Android text reply

LABEL_DIR = "voorraad_labels"  # under /config, not public like www

# Option keys
OPT_LOCATIONS = "locaties"
OPT_CATEGORIES = "categorieen"
OPT_QR = "qr"
OPT_NOTIFY = "melden_aan"
OPT_DASHBOARD = "dashboard"
OPT_SCAN = "bij_scannen"  # melding | openen | beide
SCAN_MODES = ("melding", "openen", "beide")
OPT_LABEL_ACTION = "label_actie"
OPT_LABEL_FIELD = "label_veld"
OPT_LABEL_EXTRA = "label_extra"
OPT_LABEL_WIDTH = "label_breedte"
OPT_LABEL_HEIGHT = "label_hoogte"
OPT_LABEL_MARGIN_LONG = "label_marge_lang"
OPT_LABEL_MARGIN_SHORT = "label_marge_kort"
OPT_LABEL_PAUSE = "label_pauze"  # seconds between labels
OPT_RECEIPT_ACTION = "bon_actie"
OPT_RECEIPT_FIELD = "bon_veld"
OPT_RECEIPT_EXTRA = "bon_extra"
OPT_RECEIPT_WIDTH = "bon_breedte"

# invriezen = freezer: the date is freeze date + days of the category;
# elsewhere every package needs a best-before date.
DEFAULT_LOCATIONS = [
    {"id": "vriezer", "naam": "Vriezer", "invriezen": True},
    {"id": "koelkast", "naam": "Koelkast", "invriezen": False},
    {"id": "kelder", "naam": "Kelder", "invriezen": False},
]
DEFAULT_CATEGORIES = [
    {
        "naam": "Vlees",
        "prefix": "V",
        "locatie": "vriezer",
        "dagen": 90,
        "label": True,
        "qr": True,
    },
    {
        "naam": "Restjes",
        "prefix": "R",
        "locatie": "vriezer",
        "dagen": 60,
        "label": True,
        "qr": True,
    },
]

DEFAULT_OPTIONS = {
    OPT_LOCATIONS: DEFAULT_LOCATIONS,
    OPT_CATEGORIES: DEFAULT_CATEGORIES,
    OPT_QR: False,
    OPT_NOTIFY: "",
    OPT_DASHBOARD: "/lovelace/voorraad",
    OPT_SCAN: "melding",
    OPT_LABEL_ACTION: "",
    OPT_LABEL_FIELD: "bestand",
    OPT_LABEL_EXTRA: "",
    OPT_LABEL_WIDTH: 240,
    OPT_LABEL_HEIGHT: 96,
    OPT_LABEL_MARGIN_LONG: 12,
    OPT_LABEL_MARGIN_SHORT: 5,
    OPT_LABEL_PAUSE: 5,
    OPT_RECEIPT_ACTION: "",
    OPT_RECEIPT_FIELD: "message",
    OPT_RECEIPT_EXTRA: "",
    OPT_RECEIPT_WIDTH: 42,
}
