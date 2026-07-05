"""Open-Meteo weather + BigDataCloud reverse-geocoding (no API key required).

Open-Meteo is free for non-commercial use and returns current temperature +
WMO weather code for any (lat, lon). We pair it with BigDataCloud's
reverse-geocode-client API to turn (lat, lon) into a hierarchical address
(country · province · city · district). Both APIs are key-free; the BDC
client endpoint is intended for low-volume use.

References:
  - https://open-meteo.com/en/docs
  - https://www.bigdatacloud.com/geocoding-apis/reverse-geocode-client
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("diary.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BDC_REVERSE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"
DEFAULT_TIMEOUT = 3.0  # seconds; we never block the save on weather

# WMO weather code → (Chinese description, emoji)
# https://open-meteo.com/en/docs#weathervariables
WMO_CODE_MAP: Dict[int, tuple] = {
    0:  ("晴", "☀️"),
    1:  ("少云", "🌤️"),
    2:  ("多云", "⛅"),
    3:  ("阴", "☁️"),
    45: ("雾", "🌫️"),
    48: ("冻雾", "🌫️"),
    51: ("小毛毛雨", "🌦️"),
    53: ("毛毛雨", "🌦️"),
    55: ("大毛毛雨", "🌦️"),
    56: ("冻毛毛雨", "🌦️"),
    57: ("强冻毛毛雨", "🌦️"),
    61: ("小雨", "🌧️"),
    63: ("中雨", "🌧️"),
    65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"),
    67: ("强冻雨", "🌧️"),
    71: ("小雪", "🌨️"),
    73: ("中雪", "🌨️"),
    75: ("大雪", "🌨️"),
    77: ("雪粒", "🌨️"),
    80: ("小阵雨", "🌦️"),
    81: ("中阵雨", "🌦️"),
    82: ("大阵雨", "⛈️"),
    85: ("小阵雪", "🌨️"),
    86: ("大阵雪", "🌨️"),
    95: ("雷暴", "⛈️"),
    96: ("雷暴伴小冰雹", "⛈️"),
    99: ("雷暴伴大冰雹", "⛈️"),
}


def _code_to_condition(code: int) -> tuple:
    return WMO_CODE_MAP.get(code, (f"代码 {code}", "🌡️"))


def _location_name(lat: float, lon: float) -> str:
    """Best-effort location label. We don't reverse-geocode (would need another
    API call); instead show a short coord string. Frontend can enrich later
    if we add a reverse-geocode service."""
    return f"{lat:.2f}°N {lon:.2f}°E" if lat >= 0 else f"{abs(lat):.2f}°S {lon:.2f}°E"


def _format_address(parts: List[str]) -> str:
    """Drop empty/duplicate parts and join with a middle-dot separator."""
    seen = set()
    out: List[str] = []
    for p in parts:
        s = (p or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return " · ".join(out)


async def reverse_geocode(lat: float, lon: float, *, language: str = "zh") -> Optional[Dict[str, Any]]:
    """Reverse-geocode (lat, lon) to a hierarchical address via BigDataCloud.

    Returns a dict with `address` (formatted string) and `parts` (the raw
    fields: country, state/province, city, district, locality, postcode),
    or None on any error / no result.

    Note: BDC's `/reverse-geocode-client` is intended for low-volume
    browser-side use; for high volume, switch to `/data/reverse-geocode`
    with an API key.
    """
    try:
        params = {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "localityLanguage": language,
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(BDC_REVERSE_URL, params=params)
            if resp.status_code != 200:
                logger.warning("bdc http %s", resp.status_code)
                return None
            data = resp.json()
        if not data or data.get("status") == 404 or "countryName" not in data:
            return None
        country    = data.get("countryName")
        state      = data.get("principalSubdivision")        # e.g. 浙江省
        city       = data.get("city") or data.get("locality") # 杭州市
        district   = (data.get("localityInfo", {})
                          .get("administrative", [{}])[0]
                          .get("name")) if False else None
        # BDC also nests administrative levels under localityInfo.administrative;
        # we use the most specific locality when city is missing, and the
        # principalSubdivision as the "state" if no city.
        if not district:
            admin_list = (data.get("localityInfo") or {}).get("administrative") or []
            # Last entries tend to be the most specific (district/county).
            for entry in admin_list:
                lvl = entry.get("adminLevel")
                if lvl in (6, 8, 9):  # city, district, sub-district
                    district = entry.get("name")
                    break
        locality   = data.get("locality")
        postcode   = data.get("postcode")
        parts_list = [country, state, city, district, postcode]
        return {
            "address": _format_address(parts_list),
            "parts": {
                "country": country,
                "state": state,
                "city": city,
                "district": district,
                "locality": locality,
                "postcode": postcode,
            },
        }
    except Exception as exc:
        logger.warning("reverse_geocode failed: %s", exc)
        return None


async def fetch_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetch current weather for (lat, lon) from Open-Meteo.

    Also calls the reverse-geocoding API to populate the `address` field
    with a city · district · street string. Either call may fail
    independently — the other is still used.

    Returns a dict with temp_c / condition / emoji / location / address /
    source / captured_at, or None on any error (network, parse, missing
    field). Never raises.
    """
    try:
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": "temperature_2m,weather_code",
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            if resp.status_code != 200:
                logger.warning("weather http %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
        current = data.get("current") or {}
        temp = current.get("temperature_2m")
        code = current.get("weather_code")
        if temp is None or code is None:
            return None
        cond, emoji = _code_to_condition(int(code))
        out = {
            "temp_c": float(temp),
            "weather_code": int(code),
            "condition": cond,
            "emoji": emoji,
            "location": _location_name(lat, lon),
            "source": "open-meteo",
            "captured_at": current.get("time"),
        }
    except Exception as exc:
        logger.warning("weather fetch failed: %s", exc)
        return None

    # Reverse-geocode in a separate try-block so a failure here doesn't
    # drop the weather data we already have.
    try:
        geo = await reverse_geocode(lat, lon)
        if geo and geo.get("address"):
            out["address"] = geo["address"]
            out["address_parts"] = geo["parts"]
    except Exception as exc:
        logger.warning("reverse_geocode inline failed: %s", exc)

    return out
