from datetime import datetime, timezone

import json

from app import config
from app.api.fuel_market import FuelMarketService, parse_ship_bunker, parse_usda, parse_yahoo_brent


def test_ship_bunker_parser_reads_latest_singapore_vlsfo_and_mgo():
    html = """
    <table class="price-table VLSFO"><tbody>
      <tr><th id="row-0-VLSFO" scope="row" class="date"><span>M</span> Jul 13</th>
      <td headers="price-VLSFO"><span>692.50</span></td></tr>
      <tr><th id="row-1-VLSFO" scope="row" class="date"><span>F</span> Jul 10</th>
      <td headers="price-VLSFO"><span>677.50</span></td></tr>
    </tbody></table>
    <table class="price-table MGO"><tbody>
      <tr><th id="row-0-MGO" scope="row" class="date"><span>M</span> Jul 13</th>
      <td headers="price-MGO"><span>996.00</span></td></tr>
    </tbody></table>
    """

    prices, history = parse_ship_bunker(html, datetime(2026, 7, 15, tzinfo=timezone.utc))

    assert next(item for item in prices if item["grade"] == "VLSFO")["usd_per_ton"] == 692.5
    assert next(item for item in prices if item["grade"] == "LSMGO")["usd_per_ton"] == 996
    assert next(item for item in prices if item["grade"] == "ULSFO")["estimated"] is True
    assert [item["date"] for item in history] == ["2026-07-10", "2026-07-13"]


def test_usda_parser_exposes_open_data_prices_and_real_history():
    rows = [
        {"day": "2026-07-08T00:00:00", "vlsfo_fuel_oil_imo_2020_grade_0_5": "693.50",
         "marine_gas_oil": "1135.00", "intermdiate_fuel_oil_380cst": "551.00"},
        {"day": "2026-07-07T00:00:00", "vlsfo_fuel_oil_imo_2020_grade_0_5": "676.50",
         "marine_gas_oil": "1114.00", "intermdiate_fuel_oil_380cst": "526.50"},
    ]

    prices, history = parse_usda(rows)

    assert {item["grade"] for item in prices} == {"HSHFO", "VLSFO", "LSMGO", "ULSFO", "BIO_HSFO"}
    assert history[0]["date"] == "2026-07-07"
    assert all(item["source"] == "USDA Open Ag Transport Data" for item in history)


def test_corrupt_cache_degrades_to_unavailable_instead_of_raising(tmp_path, monkeypatch):
    cache = tmp_path / "fuel.json"
    cache.write_text(json.dumps({"fetched_at": "bad-date", "prices": "not-a-list", "history": []}))
    monkeypatch.setattr(config, "FUEL_LIVE_ENABLED", False)

    result = FuelMarketService(cache).snapshot()

    assert result["market_status"] == "unavailable"
    assert result["prices"] == []


def test_yahoo_brent_fallback_is_explicitly_an_estimated_proxy():
    payload = {"chart": {"result": [{
        "timestamp": [1783987200, 1784073600],
        "indicators": {"quote": [{"close": [82.0, 84.0]}]},
    }]}}

    prices, history = parse_yahoo_brent(payload)

    assert prices[0]["grade"] == "VLSFO"
    assert prices[0]["estimated"] is True
    assert "proxy" in prices[0]["source"]
    assert prices[0]["usd_per_ton"] == round(84 * 7.53, 2)
    assert len(history) == 2
