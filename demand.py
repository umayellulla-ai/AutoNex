from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from db import db_fetch_all, db_fetch_one


MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "demand_model.pkl"


def predict_demand(stop_id):
    if MODEL_PATH.exists():
        try:
            model = joblib.load(MODEL_PATH)
            now = datetime.now()
            route = db_fetch_one("SELECT route_id FROM route_stops WHERE stop_id = ?", (stop_id,))
            route_id = route["route_id"] if route else 1
            features = pd.DataFrame([[
                stop_id,
                now.hour,
                now.weekday(),
                route_id,
                int(now.weekday() >= 5),
            ]], columns=["stop_id", "hour_of_day", "day_of_week", "route_id", "is_weekend"])
            prediction = model.predict(features)[0]
            return max(round(float(prediction), 1), 0)
        except Exception:
            pass

    rows = db_fetch_all(
        "SELECT AVG(passenger_count) AS avg_demand FROM demand_data WHERE stop_id = ?",
        (stop_id,),
    )
    if rows and rows[0]["avg_demand"] is not None:
        return round(float(rows[0]["avg_demand"]), 1)
    return 10.0