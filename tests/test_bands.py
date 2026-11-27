"""Band selection helpers."""
import pytest

from custom_components.ha_peplink.bands import (
    SOURCE_ROUTER,
    channel_width_mhz,
    load_bundled_table,
    normalize_band,
    parse_band_info,
    resolve_bands,
    same_selection,
    sim_api_id,
    sim_key,
    supported_bands,
)

TABLE = load_bundled_table()


@pytest.mark.parametrize(
    ("raw", "token"),
    [("12", "LTE_B12"), ("B2", "LTE_B2"), ("lte b66", "LTE_B66"), ("LTE_B4", "LTE_B4"),
     ("5g nsa b66", "5G_NSA_B66"), ("5G_SA_n41", "5G_SA_B41"), ("wcdma_850", "WCDMA_850")],
)
def test_normalize(raw, token):
    assert normalize_band(raw) == token


def test_auto_and_empty_mean_auto():
    assert resolve_bands([], ["LTE_B2"]) is None
    assert resolve_bands(["auto"], ["LTE_B2"]) is None
    assert resolve_bands(["AUTO"], None) is None


def test_auto_cannot_mix():
    with pytest.raises(ValueError, match="auto"):
        resolve_bands(["auto", "LTE_B2"], ["LTE_B2"])


def test_resolve_orders_like_the_router_and_dedupes():
    supported = ["LTE_B2", "LTE_B4", "LTE_B12", "LTE_B66"]
    assert resolve_bands(["66", "B2", "lte_b12", "LTE_B2"], supported) == ["LTE_B2", "LTE_B12", "LTE_B66"]


def test_unsupported_band_names_the_supported_list():
    with pytest.raises(ValueError, match="LTE_B71.*Supported: LTE_B2"):
        resolve_bands(["LTE_B71"], ["LTE_B2"])


def test_without_a_table_only_wellformed_tokens_pass():
    assert resolve_bands(["LTE_B2", "5G_NSA_B66"], None) == ["LTE_B2", "5G_NSA_B66"]
    with pytest.raises(ValueError, match="Unrecognised"):
        resolve_bands(["banana"], None)


def test_em7511_generic_rows():
    bands, exact = supported_bands(TABLE, "EM7511", "Auto (AT&T)", None)
    assert exact and len(bands) == 29
    assert "LTE_B66" in bands and "WCDMA_850" in bands
    lte, _ = supported_bands(TABLE, "EM7511", "Auto (AT&T)", "LTE")
    assert lte and all(b.startswith("LTE_") for b in lte)


def test_carrier_mode_picks_carrier_rows():
    generic, _ = supported_bands(TABLE, "EM7511", None, None)
    verizon, _ = supported_bands(TABLE, "EM7511", "Verizon Wireless", None)
    assert len(verizon) < len(generic)
    # A carrier the modem has no rows for falls back to the generic rows.
    att, _ = supported_bands(TABLE, "EM7511", "AT&T", None)
    assert att == generic


def test_model_variants_are_unioned_and_flagged():
    bands, exact = supported_bands(TABLE, "EM9190", None, None)
    assert bands and not exact
    assert any(b.startswith("5G_") for b in bands)


def test_unknown_modem():
    assert supported_bands(TABLE, "NOPE123", None, None) == (None, False)
    assert supported_bands(None, "EM7511", None, None) == (None, False)


@pytest.mark.parametrize(
    ("raw", "key"),
    [("A", "1"), ("SIM A", "1"), (1, "1"), ("sim b", "2"), ("2", "2"),
     ("eSIM", "speedfusionConnect5gLte"), ("Peplink eSIM", "speedfusionConnect5gLte"),
     ("RemoteSIM", "remoteSim"), ("fusionsim", "fusionSim")],
)
def test_sim_key(raw, key):
    assert sim_key(raw) == key


def test_sim_key_rejects_unknown():
    with pytest.raises(ValueError):
        sim_key("C")


def test_sim_api_id():
    assert sim_api_id("1") == 1
    assert sim_api_id("speedfusionConnect5gLte") == "speedfusionConnect5gLte"


def test_channel_width():
    assert channel_width_mhz("10 MHz") == 10.0
    assert channel_width_mhz("1.4 MHz") == 1.4
    assert channel_width_mhz(None) is None


def test_same_selection():
    assert same_selection(None, [])
    assert same_selection(["LTE_B4", "LTE_B2"], ["LTE_B2", "LTE_B4"])
    assert not same_selection(None, ["LTE_B2"])


def test_parse_router_band_info():
    table = parse_band_info({
        "capability": [{"mode": "EM7690", "type": "*", "freq": "*", "list": ["LTE_B2", "LTE_B4"]}],
        "description": [{"name": "LTE (Band 2)", "value": "LTE_B2"}],
    })
    assert table.source == SOURCE_ROUTER
    assert supported_bands(table, "EM7690") == (["LTE_B2", "LTE_B4"], True)
    assert table.names["LTE_B2"] == "LTE (Band 2)"
    assert parse_band_info(None) is None
    assert parse_band_info({"capability": []}) is None


from custom_components.ha_peplink.bands import band_name_from_channel, is_unknown_band_name  # noqa: E402


def test_unknown_names():
    assert is_unknown_band_name("Unknown") and is_unknown_band_name("") and is_unknown_band_name(None)
    assert not is_unknown_band_name("LTE Band 30 (WCS 2300 MHz)")


def test_lte_band_from_earfcn():
    # The reporter's secondary carrier: EARFCN 9820 is LTE Band 30.
    assert band_name_from_channel(TABLE, 9820) == "LTE (Band 30)"
    assert band_name_from_channel(TABLE, 66986) == "LTE (Band 66)"
    assert band_name_from_channel(TABLE, 5110) == "LTE (Band 12)"


def test_5g_only_when_unambiguous():
    # NR-ARFCN 126400 is only in n71's range. 5G ranges overlap a lot:
    # 520110 is in n38 and n41, 650000 in n77 and n78, so those stay unnamed.
    assert band_name_from_channel(TABLE, 126400) == "5G (Band n71)"
    assert band_name_from_channel(TABLE, 520110) is None
    assert band_name_from_channel(TABLE, 650000) is None


def test_no_table_or_channel():
    assert band_name_from_channel(None, 9820) is None
    assert band_name_from_channel(TABLE, None) is None
    assert band_name_from_channel(TABLE, 10_000_000) is None


def test_router_table_keeps_channel_ranges():
    table = parse_band_info({
        "capability": [{"mode": "EM7511", "type": "*", "freq": "*", "list": ["LTE_B30"]}],
        "description": [{"name": "LTE (Band 30)", "value": "LTE_B30", "channel": {"min": 9770, "max": 9869}}],
    })
    assert table.channels == {"LTE_B30": (9770, 9869)}
    assert band_name_from_channel(table, 9820) == "LTE (Band 30)"


from custom_components.ha_peplink.bands import corrected_earfcn  # noqa: E402


def test_truncated_earfcn_makes_low_values_ambiguous():
    # 1550 is Band 3, but 1550 + 65536 = 67086 is Band 66 (an EM7511 reports B66 that way).
    assert band_name_from_channel(TABLE, 1550) is None
    # 9820 + 65536 is not an LTE channel, so 9820 can only be Band 30.
    assert band_name_from_channel(TABLE, 9820) == "LTE (Band 30)"


def test_truncated_earfcn_is_corrected_from_the_band_name():
    assert corrected_earfcn(TABLE, "LTE Band 66 (AWS 1700/2100 MHz)", 1550) == 67086
    assert corrected_earfcn(TABLE, "LTE Band 66 (AMS 1700/2100 MHz)", 67036) == 67036
    assert corrected_earfcn(TABLE, "LTE Band 3 (1800 MHz)", 1550) == 1550
    assert corrected_earfcn(TABLE, "LTE Band 2 (1900 MHz)", 1125) == 1125
    assert corrected_earfcn(TABLE, "Unknown", 1550) == 1550
    assert corrected_earfcn(None, "LTE Band 66", 1550) == 1550


def test_modem_known_to_report_full_earfcns():
    # Ambiguous by default (Band 2, or a truncated Band 66)...
    assert band_name_from_channel(TABLE, 1125) is None
    # ...but a modem seen reporting full EARFCNs means 1125 is Band 2.
    assert band_name_from_channel(TABLE, 1125, truncates=False) == "LTE (Band 2)"
    # A modem known to truncate keeps the ambiguity.
    assert band_name_from_channel(TABLE, 1125, truncates=True) is None
