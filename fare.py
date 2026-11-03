from db import db_fetch_one


DEFAULT_FARE_SETTINGS = {"base_fare": 25.0, "rate_per_km": 12.0, "min_fare": 25.0}


def get_fare_settings():
    settings = db_fetch_one(
        "SELECT * FROM fare_settings WHERE is_active = 1 ORDER BY setting_id DESC LIMIT 1"
    )
    return settings or DEFAULT_FARE_SETTINGS.copy()


def calculate_fare(distance_km, settings=None):
    settings = settings or get_fare_settings()
    fare = float(settings["base_fare"]) + max(float(distance_km), 0.0) * float(settings["rate_per_km"])
    return max(fare, float(settings.get("min_fare") or 0))