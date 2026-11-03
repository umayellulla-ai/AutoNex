import json
import threading
import time
from math import cos, radians, sin, sqrt, atan2
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import Config


EARTH_RADIUS_KM = 6371.0
_GEOCODE_CACHE = {}
_GEOCODE_LOCK = threading.Lock()
_LAST_GEOCODE_REQUEST = 0.0


def geocode_location(address):
    global _LAST_GEOCODE_REQUEST

    normalized_address = " ".join(address.casefold().split())
    if normalized_address in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[normalized_address]

    with _GEOCODE_LOCK:
        if normalized_address in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[normalized_address]

        wait_seconds = 1.0 - (time.monotonic() - _LAST_GEOCODE_REQUEST)
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        query = urlencode({"q": address, "format": "jsonv2", "limit": 1, "countrycodes": "in"})
        lookup = Request(
            f"https://nominatim.openstreetmap.org/search?{query}",
            headers={"User-Agent": "AUTONEX/1.0 (ride booking demo)"},
        )
        with urlopen(lookup, timeout=Config.GEOCODE_TIMEOUT_SECONDS) as response:
            matches = json.loads(response.read().decode("utf-8"))
        _LAST_GEOCODE_REQUEST = time.monotonic()
        if not matches:
            return None

        location = {
            "latitude": float(matches[0]["lat"]),
            "longitude": float(matches[0]["lon"]),
            "display_name": matches[0].get("display_name", address),
        }
        _GEOCODE_CACHE[normalized_address] = location
        return location


def haversine_km(lat1, lon1, lat2, lon2):
    lat1_rad = radians(float(lat1))
    lat2_rad = radians(float(lat2))
    delta_lat = radians(float(lat2) - float(lat1))
    delta_lon = radians(float(lon2) - float(lon1))
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * atan2(sqrt(value), sqrt(1 - value))


def nearest_route_stop(latitude, longitude, stops):
    if not stops:
        return None
    return min(
        stops,
        key=lambda stop: haversine_km(
            latitude,
            longitude,
            stop["latitude"],
            stop["longitude"],
        ),
    )


def distance_along_route_km(latitude, longitude, stops):
    if not stops:
        return 0.0
    if len(stops) == 1:
        return float(stops[0].get("distance_from_start_km", 0.0))

    best_distance = float("inf")
    best_progress = 0.0
    for start, end in zip(stops, stops[1:]):
        start_lat = float(start["latitude"])
        start_lon = float(start["longitude"])
        end_lat = float(end["latitude"])
        end_lon = float(end["longitude"])
        mean_lat = radians((start_lat + end_lat) / 2)
        scale_x = 111.32 * cos(mean_lat)
        scale_y = 110.574
        segment_x = (end_lon - start_lon) * scale_x
        segment_y = (end_lat - start_lat) * scale_y
        point_x = (float(longitude) - start_lon) * scale_x
        point_y = (float(latitude) - start_lat) * scale_y
        segment_length_squared = segment_x**2 + segment_y**2
        fraction = 0.0 if segment_length_squared == 0 else (
            (point_x * segment_x + point_y * segment_y) / segment_length_squared
        )
        fraction = max(0.0, min(1.0, fraction))
        projected_x = fraction * segment_x
        projected_y = fraction * segment_y
        cross_track_distance = sqrt((point_x - projected_x) ** 2 + (point_y - projected_y) ** 2)
        if cross_track_distance < best_distance:
            segment_distance = haversine_km(start_lat, start_lon, end_lat, end_lon)
            start_progress = float(start.get("distance_from_start_km", 0.0))
            end_progress = float(end.get("distance_from_start_km", start_progress + segment_distance))
            best_distance = cross_track_distance
            best_progress = start_progress + fraction * (end_progress - start_progress)
    return best_progress


def distance_between_route_stops(pickup_stop, drop_stop):
    return abs(
        float(drop_stop["distance_from_start_km"])
        - float(pickup_stop["distance_from_start_km"])
    )