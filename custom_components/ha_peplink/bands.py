"""Cellular band selection helpers.

Pure functions with no Home Assistant imports, so they can be unit-tested alone.

Band tokens are the router's own strings: ``LTE_B2``, ``5G_NSA_B66``,
``5G_SA_B41``, ``WCDMA_850`` and so on. A SIM's ``bandSelection`` is a list of
tokens, or ``None`` for Auto.

Which bands a modem supports comes from the router's band reference table
(``config.wan.connection`` with ``reference=yes``). Rows are keyed by modem model
(as ``status.wan.connection`` reports it), network-mode carrier and the SIM's
LTE/3G setting. Firmware that doesn't return the table falls back to a bundled
copy (``band_capabilities.json``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ROUTER = "router"
SOURCE_BUNDLED = "bundled"

AUTO = "auto"

# Config SIM keys (config.wan.connection cellular.sim) -> display names.
SIM_NAMES: dict[str, str] = {
    "1": "SIM A",
    "2": "SIM B",
    "remoteSim": "RemoteSIM",
    "fusionSim": "FusionSIM",
    "speedfusionConnect5gLte": "Peplink eSIM",
}

_SIM_ALIASES: dict[str, str] = {
    "1": "1", "a": "1", "sima": "1",
    "2": "2", "b": "2", "simb": "2",
    "remotesim": "remoteSim",
    "fusionsim": "fusionSim",
    "esim": "speedfusionConnect5gLte",
    "peplinkesim": "speedfusionConnect5gLte",
    "speedfusionconnect5glte": "speedfusionConnect5gLte",
}

# status.wan.connection cellular.networkMode -> reference table "type".
# "Auto (AT&T)" and other Auto modes use the generic rows.
_CARRIER_TYPES: dict[str, str] = {
    "att": "ATT",
    "tmobile": "TMO",
    "tmo": "TMO",
    "verizon": "Verizon",
    "verizonwireless": "Verizon",
    "sprint": "Sprint",
    "generic": "Generic",
}

_TOKEN_PATTERN = re.compile(r"^(LTE_B\d+|5G_(NSA|SA)_B\d+|WCDMA_[0-9A-Z_]+|GSM_[0-9A-Z_]+|TD_SCDMA_[0-9A-Z_]+)$")


@dataclass
class BandTable:
    """Supported-band reference table."""

    capability: dict[str, dict[str, list[str]]]  # modem model -> "type|freq" -> tokens
    names: dict[str, str]                         # token -> display name
    source: str                                   # SOURCE_ROUTER or SOURCE_BUNDLED
    channels: dict[str, tuple[int, int]] = field(default_factory=dict)  # token -> (min, max) DL channel


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def parse_band_info(band_info: object, source: str = SOURCE_ROUTER) -> BandTable | None:
    """Build a BandTable from the router's ``reference.bandInfo`` object."""
    if not isinstance(band_info, dict):
        return None
    capability: dict[str, dict[str, list[str]]] = {}
    for row in band_info.get("capability") or []:
        if not isinstance(row, dict) or not isinstance(row.get("list"), list):
            continue
        mode = row.get("mode")
        if not mode:
            continue
        key = f"{row.get('type') or '*'}|{row.get('freq') or '*'}"
        capability.setdefault(str(mode), {})[key] = [str(b) for b in row["list"]]
    names = {
        str(d["value"]): str(d.get("name") or d["value"])
        for d in band_info.get("description") or []
        if isinstance(d, dict) and d.get("value")
    }
    channels: dict[str, tuple[int, int]] = {}
    for d in band_info.get("description") or []:
        rng = d.get("channel") if isinstance(d, dict) else None
        if isinstance(rng, dict) and isinstance(rng.get("min"), int) and isinstance(rng.get("max"), int):
            channels[str(d["value"])] = (rng["min"], rng["max"])
    if not capability:
        return None
    return BandTable(capability=capability, names=names, source=source, channels=channels)


def load_bundled_table() -> BandTable:
    """Load the bundled table. Blocking file I/O: run it in an executor."""
    path = Path(__file__).with_name("band_capabilities.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return BandTable(
        capability=data["capability"],
        names=data["names"],
        source=SOURCE_BUNDLED,
        channels={k: (v[0], v[1]) for k, v in data.get("channels", {}).items()},
    )


def network_type(network_mode: str | None) -> str:
    """Map the modem's network mode to a reference table carrier type."""
    if not network_mode or network_mode.strip().lower().startswith("auto"):
        return "*"
    return _CARRIER_TYPES.get(_squash(network_mode), "*")


def supported_bands(
    table: BandTable | None,
    model: str | None,
    network_mode: str | None = None,
    mobile_type: str | None = None,
) -> tuple[list[str] | None, bool]:
    """Return (bands the modem supports, exact model match).

    Picks the row for the modem's carrier mode and the SIM's LTE/3G setting,
    falling back to the generic rows. A model the table lists only as variants
    (e.g. ``EM9190`` vs ``EM9190_S1F1``) gets the union of its variants, flagged
    as not exact. (None, False) when the modem isn't in the table.
    """
    if table is None or not model:
        return None, False
    ctype = network_type(network_mode)
    freq = mobile_type or "*"
    keys = (f"{ctype}|{freq}", f"{ctype}|*", f"*|{freq}", "*|*")

    def pick(rows: dict[str, list[str]]) -> list[str] | None:
        for key in keys:
            if key in rows:
                return list(rows[key])
        return max(rows.values(), key=len) if rows else None

    rows = table.capability.get(model)
    if rows:
        return pick(rows), True

    variants = [r for m, r in table.capability.items() if m.startswith(f"{model}_")]
    if not variants:
        return None, False
    union: list[str] = []
    for rows in variants:
        for band in pick(rows) or []:
            if band not in union:
                union.append(band)
    return union, False


def sim_key(value: object) -> str:
    """Map a user-supplied SIM name ('A', 'SIM B', 2, 'eSIM', ...) to a config key."""
    key = _SIM_ALIASES.get(_squash(str(value)))
    if key is None:
        raise ValueError(
            f"Unknown SIM '{value}'. Use one of: SIM A, SIM B, RemoteSIM, FusionSIM, Peplink eSIM"
        )
    return key


def sim_api_id(key: str) -> int | str:
    """SIM id as the write API expects it: 1/2 as numbers, others as strings."""
    return int(key) if key.isdigit() else key


def normalize_band(value: str) -> str:
    """Normalize shorthand like '12', 'B12', 'lte b12', '5g_sa_n41' to router tokens."""
    raw = str(value).strip()
    u = re.sub(r"[\s\-]+", "_", raw.upper())
    if m := re.fullmatch(r"(?:LTE_?)?B?(\d+)", u):
        return f"LTE_B{int(m.group(1))}"
    if m := re.fullmatch(r"5G_?(NSA|SA)_?[BN]?(\d+)", u):
        return f"5G_{m.group(1)}_B{int(m.group(2))}"
    return u


def resolve_bands(requested: list[str], supported: list[str] | None) -> list[str] | None:
    """Validate a requested band list. Returns None for Auto.

    An empty list, or ['auto'], means Auto. Raises ValueError naming any band
    the modem doesn't support (or, with no table, any unrecognisable token).
    The result follows the router's own band order.
    """
    items = [str(b).strip() for b in requested if str(b).strip()]
    if not items or (len(items) == 1 and items[0].lower() == AUTO):
        return None
    if any(b.lower() == AUTO for b in items):
        raise ValueError("'auto' can't be combined with specific bands")

    by_upper = {b.upper(): b for b in supported or []}
    result: list[str] = []
    unknown: list[str] = []
    for item in items:
        token = normalize_band(item)
        token = by_upper.get(token, token)
        if supported is not None:
            if token not in supported:
                unknown.append(item)
                continue
        elif not _TOKEN_PATTERN.fullmatch(token):
            unknown.append(item)
            continue
        if token not in result:
            result.append(token)

    if unknown:
        if supported is not None:
            raise ValueError(
                f"Not supported by this modem: {', '.join(unknown)}. Supported: {', '.join(supported)}"
            )
        raise ValueError(
            f"Unrecognised band(s): {', '.join(unknown)}. Use tokens like LTE_B12, 5G_NSA_B66, WCDMA_850"
        )
    if supported is not None:
        result.sort(key=supported.index)
    return result


def same_selection(a: list[str] | None, b: list[str] | None) -> bool:
    """Compare two band selections, treating None and [] as Auto."""
    return sorted(a or []) == sorted(b or [])


def channel_width_mhz(value: object) -> float | None:
    """'10 MHz' -> 10.0; '1.4 MHz' -> 1.4."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None

_UNKNOWN_BAND_NAMES = {"", "unknown", "n/a", "-"}


def is_unknown_band_name(name: str | None) -> bool:
    """True when the router didn't name a carrier's band."""
    return name is None or name.strip().lower() in _UNKNOWN_BAND_NAMES


# Some modems (seen on a Sierra EM7511) report LTE EARFCNs above 65535 modulo
# 65536: Band 66's 67086 arrives as 1550. A value below 65536 may therefore be
# the low 16 bits of a larger EARFCN.
_EARFCN_WRAP = 65536


def _lte_band(table: BandTable, channel: int) -> str | None:
    hits = [
        t for t, (lo, hi) in table.channels.items()
        if t.startswith("LTE_") and lo <= channel <= hi
    ]
    return hits[0] if len(hits) == 1 else None


def band_name_from_channel(
    table: BandTable | None, channel: int | None, truncates: bool | None = None
) -> str | None:
    """Name a carrier's band from its downlink channel, using the router's table.

    LTE (EARFCN, below ~70,000) and 5G NR (NR-ARFCN, from 123,400) channel
    numbers don't overlap, and LTE bands have disjoint ranges. A reported
    EARFCN may be truncated to 16 bits, so both readings are tried, and the
    band is named only when they agree (or only one is a valid EARFCN).
    *truncates* is what this modem is known to do (False: it reports full
    EARFCNs, so only the reported value is used; None: unknown). Several
    5G bands share ranges (n77 contains n78), so a 5G name is only given when
    exactly one band matches. Returns None when unsure.
    """
    if table is None or channel is None or not table.channels:
        return None
    readings = [channel]
    if channel < _EARFCN_WRAP and truncates is not False:
        readings.append(channel + _EARFCN_WRAP)
    candidates = {b for b in (_lte_band(table, r) for r in readings) if b}
    if len(candidates) == 1:
        token = candidates.pop()
        return table.names.get(token, token)
    if candidates:
        return None
    matches = [t for t, (lo, hi) in table.channels.items() if lo <= channel <= hi]
    nr_bands = {m.group(1) for t in matches if (m := re.fullmatch(r"5G_(?:NSA|SA)_B(\d+)", t))}
    if len(nr_bands) == 1 and not any(t.startswith("LTE_") for t in matches):
        return f"5G (Band n{nr_bands.pop()})"
    return None


def corrected_earfcn(table: BandTable | None, band_name: str | None, channel: int | None) -> int | None:
    """Undo a 16-bit-truncated EARFCN when the router's band name shows the real band.

    "LTE Band 66 ..." with EARFCN 1550: 1550 is outside Band 66, 1550 + 65536
    = 67086 is inside it, so 67086 is the real channel. Anything else is
    returned unchanged.
    """
    if table is None or channel is None or not band_name or channel >= _EARFCN_WRAP:
        return channel
    m = re.search(r"\bLTE\b.*?\bBand\s+(\d+)", band_name, re.IGNORECASE) or re.search(
        r"\bB(\d+)\b", band_name
    )
    rng = table.channels.get(f"LTE_B{int(m.group(1))}") if m else None
    if not rng:
        return channel
    lo, hi = rng
    if not lo <= channel <= hi and lo <= channel + _EARFCN_WRAP <= hi:
        return channel + _EARFCN_WRAP
    return channel
