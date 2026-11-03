from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd


MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "eta_model.pkl"
DEFAULT_SPEED_KMH = 18.0


def physics_eta_minutes(distance_km, speed_kmh=DEFAULT_SPEED_KMH):
    safe_speed = max(float(speed_kmh or DEFAULT_SPEED_KMH), 1.0)
    return max((max(float(distance_km), 0.0) / safe_speed) * 60.0, 2.0)


def predict_eta_minutes(distance_km, route_id, speed_kmh=DEFAULT_SPEED_KMH):
    if MODEL_PATH.exists():
        try:
            model = joblib.load(MODEL_PATH)
            now = datetime.now()
            features = pd.DataFrame([[
                distance_km,
                now.hour,
                now.weekday(),
                1.0,
                route_id,
            ]], columns=["distance_km", "hour_of_day", "day_of_week", "traffic_index", "route_id"])
            return max(float(model.predict(features)[0]), 2.0)
        except Exception:
            pass
    return round(physics_eta_minutes(distance_km, speed_kmh), 2)


def estimate_eta(distance_km, route_id, speed_kmh=DEFAULT_SPEED_KMH):
    formula_eta = round(physics_eta_minutes(distance_km, speed_kmh), 2)
    ml_eta = round(predict_eta_minutes(distance_km, route_id, speed_kmh), 2)
    return {"formula_eta_min": formula_eta, "ml_eta_min": ml_eta}