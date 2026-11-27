"""Coordinator band set/get flow, with a fake API."""
import asyncio

import pytest

from custom_components.ha_peplink import coordinator as cmod
from custom_components.ha_peplink.api import _parse_cellular_info
from custom_components.ha_peplink.bands import load_bundled_table
from custom_components.ha_peplink.coordinator import BandSelectionError, PeplinkCoordinator
from custom_components.ha_peplink.models import CellularBandConfig, PeplinkData, WanConnection

from test_api import STATUS


def _wan(message="Standby", with_bands=True):
    status = dict(STATUS)
    if not with_bands:
        status["rat"] = []
    return WanConnection(conn_id=3, name="Cellular 2", wan_type="cellular", enabled=True,
                         message=message, priority=2, uptime=0, ip=None, status_led=None,
                         cellular=_parse_cellular_info(status))


class FakeApi:
    def __init__(self, sims=None, ignore_writes=False, statuses=None):
        self.sims = sims or {"1": None, "2": None}
        self.ignore_writes = ignore_writes
        self.writes = []
        self.statuses = list(statuses or [])

    async def get_cellular_band_config(self, ids):
        return {3: CellularBandConfig(conn_id=3, sims=dict(self.sims), mobile_types={})}

    async def set_band_selection(self, conn_id, sim, bands):
        self.writes.append((conn_id, sim, bands))
        if not self.ignore_writes:
            self.sims[sim] = bands

    async def get_wan_status(self, ids):
        wan = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {3: wan}


def _coordinator(api):
    c = object.__new__(PeplinkCoordinator)
    c.api = api
    c.band_table = load_bundled_table()
    c._band_config = {}
    c._band_lock = asyncio.Lock()
    c._earfcn_truncates = {}
    c.wan_connections = {3: _wan()}
    c.data = PeplinkData(wan_connections={3: _wan()}, wan_usage={}, diagnostics=None, device_info=None,
                         firmware_version=None, connected_devices=None, traffic_stats={},
                         vpn_profiles={}, location=None)
    c.async_set_updated_data = lambda data: None

    async def refresh():
        return None

    c.async_request_refresh = refresh
    return c


def test_set_defaults_to_active_sim_and_verifies():
    api = FakeApi()
    result = asyncio.run(_coordinator(api).async_set_bands(3, None, ["66", "2"]))
    assert api.writes == [(3, "1", ["LTE_B2", "LTE_B66"])]
    assert result["changed"] is True
    assert result["sim"] == "SIM A" and result["previous"] == "auto"
    assert result["applied"] == ["LTE_B2", "LTE_B66"] and result["verified"] is True


def test_restore_to_auto():
    api = FakeApi(sims={"1": ["LTE_B2"], "2": None})
    result = asyncio.run(_coordinator(api).async_set_bands(3, "SIM A", "auto".split()))
    assert api.writes == [(3, "1", None)]
    assert result["previous"] == ["LTE_B2"] and result["applied"] == "auto"


def test_unsupported_band_is_rejected_before_writing():
    api = FakeApi()
    with pytest.raises(BandSelectionError, match="Not supported"):
        asyncio.run(_coordinator(api).async_set_bands(3, "A", ["LTE_B99"]))
    assert api.writes == []


def test_missing_sim_is_rejected():
    api = FakeApi(sims={"1": None})
    with pytest.raises(BandSelectionError, match="SIM B is not available"):
        asyncio.run(_coordinator(api).async_set_bands(3, "B", ["LTE_B2"]))


def test_readback_mismatch_raises():
    api = FakeApi(ignore_writes=True)
    with pytest.raises(BandSelectionError, match="reports auto"):
        asyncio.run(_coordinator(api).async_set_bands(3, "A", ["LTE_B2"]))


def test_wait_sees_drop_then_reconnect(monkeypatch):
    monkeypatch.setattr(cmod, "_LINK_POLL_S", 0)
    api = FakeApi(statuses=[_wan("Standby"), _wan("Connecting...", with_bands=False), _wan("Standby")])
    result = asyncio.run(_coordinator(api).async_set_bands(3, "A", ["LTE_B2"], wait_seconds=30))
    assert result["link"]["connected"] is True and result["link"]["restart_seen"] is True
    assert result["link"]["bands"]


def test_wait_times_out_when_link_stays_down(monkeypatch):
    monkeypatch.setattr(cmod, "_LINK_POLL_S", 0.01)
    api = FakeApi(statuses=[_wan("Connecting...", with_bands=False)])
    result = asyncio.run(_coordinator(api).async_set_bands(3, "A", ["LTE_B2"], wait_seconds=1))
    assert result["link"]["connected"] is False and result["link"]["status"] == "Connecting..."


def test_get_bands_reports_each_sim():
    api = FakeApi(sims={"1": ["LTE_B2"], "2": None})
    result = asyncio.run(_coordinator(api).async_get_bands(3))
    assert result["modem"] == "EM7511" and result["active_sim"] == "SIM A"
    assert result["sims"]["SIM A"]["bands"] == ["LTE_B2"]
    assert result["sims"]["SIM B"]["bands"] == "auto"
    assert len(result["sims"]["SIM A"]["supported"]) == 29
    assert result["band_list_source"] == "bundled"
    assert result["carriers"][0]["earfcn"] == 66986


def test_unchanged_selection_skips_the_write():
    api = FakeApi(sims={"1": ["LTE_B2", "LTE_B66"], "2": None})
    result = asyncio.run(_coordinator(api).async_set_bands(3, "A", ["66", "2"], wait_seconds=120))
    assert api.writes == [] and result["changed"] is False and "link" not in result
    result = asyncio.run(_coordinator(api).async_set_bands(3, "B", ["auto"]))
    assert api.writes == [] and result["applied"] == "auto"


def test_wait_does_not_trust_an_early_up(monkeypatch):
    """The drop comes ~25-30 s after the write; an 'up' before that is not the reconnect."""
    clock = [0.0]
    monkeypatch.setattr(cmod.time, "monotonic", lambda: clock[0])

    async def fake_sleep(_s):
        clock[0] += 2

    monkeypatch.setattr(cmod.asyncio, "sleep", fake_sleep)
    up, down = _wan("Standby"), _wan("Connecting...", with_bands=False)
    # up for 28 s, down for 16 s, then up
    api = FakeApi(statuses=[up] * 15 + [down] * 8 + [up])
    c = _coordinator(api)
    result = asyncio.run(c._async_wait_for_link(3, 180))
    assert result["restart_seen"] is True and result["connected"] is True
    assert result["seconds"] >= 44


def test_unknown_secondary_carrier_is_named_from_its_channel():
    import dataclasses  # noqa: PLC0415

    c = _coordinator(FakeApi())
    wan = _wan()
    unknown = dataclasses.replace(wan.cellular.components[1], name="Unknown", earfcn=9820)
    wan = dataclasses.replace(
        wan,
        cellular=dataclasses.replace(
            wan.cellular,
            components=[wan.cellular.components[0], unknown],
            bands=[wan.cellular.components[0].name, "Unknown"],
        ),
    )
    out = c._label_unknown_bands({3: wan})[3].cellular
    assert out.components[1].name == "LTE (Band 30)" and out.components[1].name_from_channel
    assert out.components[0].name_from_channel is False
    assert out.bands[1] == "LTE (Band 30)"


def _with_components(wan, comps):
    import dataclasses  # noqa: PLC0415

    return dataclasses.replace(
        wan, cellular=dataclasses.replace(wan.cellular, components=comps, bands=[x.name for x in comps])
    )


def test_truncated_earfcn_of_a_named_carrier_is_corrected():
    import dataclasses  # noqa: PLC0415

    c = _coordinator(FakeApi())
    wan = _wan()
    b66 = dataclasses.replace(wan.cellular.components[0], name="LTE Band 66 (AWS 1700/2100 MHz)", earfcn=1550)
    out = c._label_unknown_bands({3: _with_components(wan, [b66])})[3].cellular
    assert out.components[0].earfcn == 67086
    assert c._earfcn_truncates[3] is True


def test_full_earfcn_modem_names_low_unknown_carriers():
    import dataclasses  # noqa: PLC0415

    c = _coordinator(FakeApi())
    wan = _wan()
    b66 = dataclasses.replace(wan.cellular.components[0], name="LTE Band 66 (AWS 1700/2100 MHz)", earfcn=67036)
    unknown = dataclasses.replace(wan.cellular.components[1], name="Unknown", earfcn=1125)
    out = c._label_unknown_bands({3: _with_components(wan, [b66, unknown])})[3].cellular
    assert c._earfcn_truncates[3] is False
    assert out.components[1].name == "LTE (Band 2)" and out.components[1].name_from_channel
