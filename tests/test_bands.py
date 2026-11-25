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
