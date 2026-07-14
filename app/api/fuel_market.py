"""Source-aware bunker market prices with bounded refresh and honest fallback."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from app import config

SHIP_BUNKER_URL = "https://shipandbunker.com/prices/apac/sea/sg-sin-singapore"
USDA_URL = "https://agtransport.usda.gov/resource/4v3x-mj86.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/BZ=F"
BRENT_BARRELS_PER_METRIC_TON = 7.53


def _iso_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_market_date(label: str, now: datetime) -> str:
    parsed = datetime.strptime(label.strip(), "%b %d").replace(year=now.year, tzinfo=timezone.utc)
    if parsed > now.replace(hour=0, minute=0, second=0, microsecond=0):
        parsed = parsed.replace(year=now.year - 1)
    return parsed.date().isoformat()


def _parse_ship_bunker_all(html: str, now: datetime) -> tuple[list[dict], dict[str, list[dict]]]:
    """Parse current prices and per-grade histories from the public tables."""
    grades = {
        "VLSFO": "VLSFO",
        "LSMGO": "MGO",
        "HSHFO": "IFO380",
        "BIO_HSFO": "BIO",
    }
    prices: list[dict] = []
    history_by_grade: dict[str, list[dict]] = {}
    for display_grade, table_grade in grades.items():
        table_match = re.search(
            rf'<table[^>]*class="price-table\s+{re.escape(table_grade)}"[^>]*>(.*?)</table>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not table_match:
            continue
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), flags=re.IGNORECASE | re.DOTALL)
        parsed_rows: list[tuple[str, float]] = []
        for row in rows:
            date_match = re.search(r'class="date"[^>]*>.*?([A-Z][a-z]{2}\s+\d{1,2})</th>', row, re.DOTALL)
            price_match = re.search(
                rf'<td[^>]*headers="price-{re.escape(table_grade)}"[^>]*>.*?([0-9]+(?:\.[0-9]+)?)',
                row,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if date_match and price_match:
                parsed_rows.append((_parse_market_date(date_match.group(1), now), float(price_match.group(1))))
        if not parsed_rows:
            continue
        as_of, value = parsed_rows[0]
        prices.append({
            "grade": display_grade,
            "usd_per_ton": value,
            "source": "Ship & Bunker Singapore",
            "source_url": SHIP_BUNKER_URL,
            "as_of": as_of,
            "estimated": False,
        })
        history_by_grade[display_grade] = [
            {"date": date, "usd_per_ton": price, "source": "Ship & Bunker Singapore"}
            for date, price in reversed(parsed_rows[:30])
        ]
    if prices:
        mgo = next((item for item in prices if item["grade"] == "LSMGO"), None)
        if mgo:
            prices.append({**mgo, "grade": "ULSFO", "source": "LSMGO proxy", "estimated": True})
            history_by_grade["ULSFO"] = [
                {**point, "source": "LSMGO proxy", "estimated": True}
                for point in history_by_grade.get("LSMGO", [])
            ]
    return prices, history_by_grade


def parse_ship_bunker(html: str, now: datetime) -> tuple[list[dict], list[dict]]:
    """Backward-compatible parser used by the focused source tests."""
    prices, history_by_grade = _parse_ship_bunker_all(html, now)
    history = [
        {"date": point["date"], "vlsfo_usd_per_ton": point["usd_per_ton"], "source": point["source"]}
        for point in history_by_grade.get("VLSFO", [])
    ]
    return prices, history


def _parse_usda_all(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    if not rows:
        return [], {}
    mapping = {
        "HSHFO": "intermdiate_fuel_oil_380cst",
        "VLSFO": "vlsfo_fuel_oil_imo_2020_grade_0_5",
        "LSMGO": "marine_gas_oil",
    }
    latest = rows[0]
    as_of = str(latest["day"])[:10]
    prices = [
        {
            "grade": grade,
            "usd_per_ton": float(latest[column]),
            "source": "USDA Open Ag Transport Data",
            "source_url": USDA_URL,
            "as_of": as_of,
            "estimated": False,
        }
        for grade, column in mapping.items()
        if latest.get(column) not in {None, ""}
    ]
    mgo = next((item for item in prices if item["grade"] == "LSMGO"), None)
    hshfo = next((item for item in prices if item["grade"] == "HSHFO"), None)
    if mgo:
        prices.append({**mgo, "grade": "ULSFO", "source": "LSMGO proxy", "estimated": True})
    if hshfo:
        prices.append({**hshfo, "grade": "BIO_HSFO", "usd_per_ton": round(hshfo["usd_per_ton"] * 1.18, 2),
                       "source": "HSFO + bio blend scenario", "estimated": True})
    history_by_grade = {
        grade: [
            {
                "date": str(row["day"])[:10],
                "usd_per_ton": float(row[column]),
                "source": "USDA Open Ag Transport Data",
            }
            for row in reversed(rows)
            if row.get(column) not in {None, ""}
        ]
        for grade, column in mapping.items()
    }
    history_by_grade["ULSFO"] = [
        {**point, "source": "LSMGO proxy", "estimated": True}
        for point in history_by_grade.get("LSMGO", [])
    ]
    history_by_grade["BIO_HSFO"] = [
        {**point, "usd_per_ton": round(point["usd_per_ton"] * 1.18, 2),
         "source": "HSFO + bio blend scenario", "estimated": True}
        for point in history_by_grade.get("HSHFO", [])
    ]
    return prices, history_by_grade


def parse_usda(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    prices, history_by_grade = _parse_usda_all(rows)
    history = [
        {"date": point["date"], "vlsfo_usd_per_ton": point["usd_per_ton"], "source": point["source"]}
        for point in history_by_grade.get("VLSFO", [])
    ]
    return prices, history


def parse_yahoo_brent(payload: dict) -> tuple[list[dict], list[dict]]:
    """Use real Brent futures only as an explicitly estimated mass-equivalent proxy."""
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    observations = [
        (datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(), float(close))
        for timestamp, close in zip(timestamps, closes)
        if close is not None
    ]
    if not observations:
        return [], []
    as_of, close = observations[-1]
    converted = round(close * BRENT_BARRELS_PER_METRIC_TON, 2)
    prices = [{
        "grade": "VLSFO",
        "usd_per_ton": converted,
        "source": "Yahoo Finance Brent futures mass-equivalent proxy",
        "source_url": YAHOO_QUOTE_URL,
        "as_of": as_of,
        "estimated": True,
    }]
    history = [
        {
            "date": day,
            "vlsfo_usd_per_ton": round(price * BRENT_BARRELS_PER_METRIC_TON, 2),
            "source": "Yahoo Finance Brent futures mass-equivalent proxy",
        }
        for day, price in observations
    ]
    return prices, history


class FuelMarketService:
    def __init__(
        self,
        cache_path: Path,
        now: Callable[[], datetime] = _iso_now,
        client_factory: Callable[[], httpx.Client] | None = None,
    ):
        self.cache_path = cache_path
        self.now = now
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=config.FUEL_HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                                 headers={"User-Agent": "HullWatch/1.0 (+source-attributed fuel dashboard)"})
        )

    def snapshot(self) -> dict:
        cached = self._read_cache()
        age = self._age_hours(cached)
        if cached and age < config.FUEL_REFRESH_HOURS:
            status = "stale" if self._source_age_hours(cached) >= config.FUEL_STALE_HOURS else "cached"
            return self._with_status(cached, status)
        if config.FUEL_LIVE_ENABLED:
            try:
                fresh = self._fetch()
                self._write_cache(fresh)
                status = "stale" if self._source_age_hours(fresh) >= config.FUEL_STALE_HOURS else "live"
                return self._with_status(fresh, status)
            except (httpx.HTTPError, KeyError, ValueError, OSError):
                pass
        if cached:
            status = "stale" if max(age, self._source_age_hours(cached)) >= config.FUEL_STALE_HOURS else "cached"
            return self._with_status(cached, status)
        return self._unavailable()

    def _fetch(self) -> dict:
        now = self.now()
        with self.client_factory() as client:
            prices: list[dict] = []
            ship_history: dict[str, list[dict]] = {}
            usda_prices: list[dict] = []
            usda_history: dict[str, list[dict]] = {}
            yahoo_prices: list[dict] = []
            yahoo_history: list[dict] = []
            try:
                ship_response = client.get(SHIP_BUNKER_URL)
                ship_response.raise_for_status()
                prices, ship_history = _parse_ship_bunker_all(ship_response.text, now)
            except (httpx.HTTPError, ValueError):
                pass
            try:
                usda_response = client.get(USDA_URL, params={"$limit": 30, "$order": "day DESC"})
                usda_response.raise_for_status()
                usda_prices, usda_history = _parse_usda_all(usda_response.json())
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            if not prices and not usda_prices:
                try:
                    yahoo_response = client.get(YAHOO_CHART_URL, params={"range": "1mo", "interval": "1d"})
                    yahoo_response.raise_for_status()
                    yahoo_prices, yahoo_history = parse_yahoo_brent(yahoo_response.json())
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    pass
        if not prices:
            prices = usda_prices or yahoo_prices
        if not prices:
            raise ValueError("No market rows returned")
        vlsfo = next((item for item in prices if item["grade"] == "VLSFO"), None)
        if not vlsfo:
            raise ValueError("VLSFO is required")
        if vlsfo["source"].startswith("Ship & Bunker"):
            port = "Singapore"
        elif vlsfo["source"].startswith("USDA"):
            port = "Global 20 Ports Average"
        else:
            port = "Brent proxy"
        return {
            "port": port,
            "currency": "USD",
            "unit": "mt",
            "prices": prices,
            "history": [
                {"date": point["date"], "vlsfo_usd_per_ton": point["usd_per_ton"], "source": point["source"]}
                for point in (ship_history or usda_history).get("VLSFO", [])
            ] or yahoo_history,
            "history_by_grade": ship_history or usda_history or {
                "VLSFO": [
                    {"date": point["date"], "usd_per_ton": point["vlsfo_usd_per_ton"],
                     "source": point["source"], "estimated": True}
                    for point in yahoo_history
                ]
            },
            "effective_price": {
                "usd_per_ton": vlsfo["usd_per_ton"],
                "method": f"{port} VLSFO latest published indication",
                "estimated": bool(vlsfo["estimated"]),
            },
            "fetched_at": now.isoformat(),
        }

    def _unavailable(self) -> dict:
        return self._with_status({
            "port": "Singapore",
            "currency": "USD",
            "unit": "mt",
            "prices": [],
            "history": [],
            "history_by_grade": {},
            "effective_price": {
                "usd_per_ton": config.VLSFO_PRICE_USD,
                "method": "manual scenario price; live market unavailable",
                "estimated": True,
            },
            "fetched_at": None,
        }, "unavailable")

    def _read_cache(self) -> dict | None:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            if not isinstance(value.get("prices"), list) or not isinstance(value.get("history"), list):
                return None
            if not isinstance(value.get("effective_price"), dict):
                return None
            return value
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, data: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _age_hours(self, data: dict | None) -> float:
        if not data or not data.get("fetched_at"):
            return float("inf")
        try:
            fetched = datetime.fromisoformat(str(data["fetched_at"]))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            return max(0.0, (self.now() - fetched).total_seconds() / 3600)
        except (TypeError, ValueError):
            return float("inf")

    def _source_age_hours(self, data: dict | None) -> float:
        try:
            prices = (data or {}).get("prices", [])
            if not isinstance(prices, list):
                return float("inf")
            dates = [item.get("as_of") for item in prices if isinstance(item, dict) and item.get("as_of")]
            if not dates:
                return float("inf")
            latest = datetime.fromisoformat(str(max(dates)))
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            return max(0.0, (self.now() - latest).total_seconds() / 3600)
        except (TypeError, ValueError):
            return float("inf")

    @staticmethod
    def _with_status(data: dict, status: str) -> dict:
        history_by_grade = data.get("history_by_grade")
        if not isinstance(history_by_grade, dict):
            vlsfo_history = [
                {"date": point["date"], "usd_per_ton": point["vlsfo_usd_per_ton"],
                 "source": point["source"]}
                for point in data.get("history", [])
                if isinstance(point, dict) and "vlsfo_usd_per_ton" in point
            ]
            history_by_grade = {"VLSFO": vlsfo_history}
            current_vlsfo = next(
                (price for price in data.get("prices", []) if price.get("grade") == "VLSFO"), None
            )
            if current_vlsfo and current_vlsfo.get("usd_per_ton"):
                for price in data.get("prices", []):
                    grade = price.get("grade")
                    if not grade or grade == "VLSFO":
                        continue
                    ratio = float(price["usd_per_ton"]) / float(current_vlsfo["usd_per_ton"])
                    history_by_grade[grade] = [
                        {**point, "usd_per_ton": round(point["usd_per_ton"] * ratio, 2),
                         "source": f"{grade} current spread × VLSFO history proxy",
                         "estimated": True}
                        for point in vlsfo_history
                    ]
        return {
            **data,
            "history_by_grade": history_by_grade,
            "market_status": status,
            "refresh_interval_hours": config.FUEL_REFRESH_HOURS,
            "stale_after_hours": config.FUEL_STALE_HOURS,
        }
