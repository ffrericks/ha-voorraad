"""Constants for the freezer inventory integration."""

DOMAIN = "vriezer"
STORAGE_KEY = "vriezer"
STORAGE_VERSION = 1
SIGNAL_UPDATED = "vriezer_updated"

# Removed packages stay in storage this long (history, reprint, undo)
HISTORY_DAYS = 365
# A notification reply ("Deel eruit") belongs to the last scan within this window
SCAN_MEMORY_MINUTES = 15

TAG_PREFIX = "vz"  # tag id = "vz" + code, e.g. vzV-0004
TAG_URL = "https://www.home-assistant.io/tag/"

ACTION_ALL = "VRIEZER_OP"  # notification action "VRIEZER_OP|V-0004"
ACTION_UNDO = "VRIEZER_TERUG"
ACTION_REPLY = "REPLY"  # Android text reply

LABEL_DIR = "vriezer_labels"  # under /config, not public like www

# Option keys
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
OPT_LABEL_PAUSE = (
    "label_pauze"  # seconds between labels (the Fichero chokes on a fast series)
)
OPT_RECEIPT_ACTION = "bon_actie"
OPT_RECEIPT_FIELD = "bon_veld"
OPT_RECEIPT_EXTRA = "bon_extra"
OPT_RECEIPT_WIDTH = "bon_breedte"

DEFAULT_CATEGORIES = [
    {"naam": "Vlees", "prefix": "V", "dagen": 90, "qr": True},
    {"naam": "Restjes", "prefix": "R", "dagen": 60, "qr": True},
]

DEFAULT_OPTIONS = {
    OPT_CATEGORIES: DEFAULT_CATEGORIES,
    OPT_QR: False,
    OPT_NOTIFY: "",
    OPT_DASHBOARD: "/lovelace/vriezer",
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
