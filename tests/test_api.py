"""Cellular status parsing and band config API calls."""
import asyncio

from custom_components.ha_peplink.api import PeplinkApiClient, PeplinkApiError, _parse_cellular_info

STATUS = {
    "network": "LTE", "mobileType": "LTE", "signalLevel": 4, "carrierAggregation": True,
    "carrier": {"name": "AT&T"},
    "sim": {"1": {"active": True, "simCardDetected": True}, "2": {"active": False}, "order": [1, 2]},
    "rat": [{"name": "", "band": [
        {"name": "LTE Band 66 (AWS 1700/2100 MHz)", "channel": 66986, "pci": 140, "channelWidth": "20 MHz",
         "signal": {"rssi": -73, "sinr": 9.4, "rsrp": -104, "rsrq": -13.0}},
        {"name": "LTE Band 2 (1900 MHz)", "channel": 1000, "pci": 140, "signal": {"rssi": -80, "rsrp": -110}},
    ]}],
    "networkMode": "Auto (AT&T)", "dataTechnology": "FDD", "mcc": "310", "mnc": "410",
    "cellTower": {"cellId": 12345678, "tac": 4321},
    "manufacturer": "Sierra Wireless, Incorporated", "model": "EM7511", "firmware": "01.14.22.00",
    "hardwareRevision": "1.0",
}


def test_parse_components_and_modem():
    cell = _parse_cellular_info(STATUS)
    assert cell.bands == ["LTE Band 66 (AWS 1700/2100 MHz)", "LTE Band 2 (1900 MHz)"]
    primary, secondary = cell.components
    assert (primary.earfcn, primary.pci, primary.bandwidth_mhz) == (66986, 140, 20.0)
    assert (primary.rsrp, primary.rsrq, primary.sinr, primary.rssi) == (-104, -13.0, 9.4, -73)
    assert secondary.bandwidth_mhz is None and secondary.sinr is None
    assert (cell.rssi_dbm, cell.rsrp_dbm) == (-73, -104)
    assert cell.modem_model == "EM7511" and cell.modem_firmware == "01.14.22.00"
    assert cell.active_sim == "1"
    assert (cell.cell_id, cell.tac, cell.mcc, cell.mnc) == (12345678, 4321, "310", "410")


def test_parse_without_rat_or_tower():
    cell = _parse_cellular_info({"model": "EM7411", "sim": {"1": {"active": False}}})
    assert cell.components == [] and cell.bands == []
    assert cell.active_sim is None and cell.cell_id is None


class _Client(PeplinkApiClient):
    def __init__(self, reply):
        self.calls = []
        self._reply = reply

    async def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self._reply


def test_band_config_read():
    client = _Client({"stat": "ok", "response": {
        "2": {"cellular": {"sim": {
            "1": {"bandSelection": ["LTE_B2", "LTE_B66"], "mobileType": "LTE"},
            "2": {"simPin": "***"},
            "order": ["1", "2"],
        }}},
        "order": [2],
    }})
    cfg = asyncio.run(client.get_cellular_band_config([2, 3]))[2]
    assert cfg.sims == {"1": ["LTE_B2", "LTE_B66"], "2": None}
    assert cfg.mobile_types == {"1": "LTE", "2": None}
    assert client.calls[0][1] == "/api/config.wan.connection?id=2 3&infoType=cellular&configType=active"


def test_band_write_payload_is_minimal():
    client = _Client({"stat": "ok"})
    asyncio.run(client.set_band_selection(2, "1", ["LTE_B2"]))
    asyncio.run(client.set_band_selection(3, "speedfusionConnect5gLte", None))
    (_, path, body), (_, _, body2) = client.calls
    assert path == "/api/config.wan.connection"
    assert body == {"action": "update", "instantActive": True, "list": [
        {"id": 2, "cellular": {"sim": [{"id": 1, "bandSelection": ["LTE_B2"]}]}}]}
    sim = body2["list"][0]["cellular"]["sim"][0]
    assert sim == {"id": "speedfusionConnect5gLte", "bandSelection": None}  # no carrierSelection


def test_band_write_refused():
    client = _Client({"stat": "fail", "code": 403, "message": "Permission denied"})
    try:
        asyncio.run(client.set_band_selection(2, "1", ["LTE_B2"]))
    except PeplinkApiError as err:
        assert "Permission denied" in str(err)
    else:
        raise AssertionError("expected PeplinkApiError")


def test_band_reference():
    client = _Client({"stat": "ok", "response": {"reference": {"bandInfo": {"capability": []}}}})
    assert asyncio.run(client.get_band_reference(2)) == {"capability": []}
    client = _Client({"stat": "ok", "response": {"2": {}}})  # firmware without the table
    assert asyncio.run(client.get_band_reference(2)) is None
