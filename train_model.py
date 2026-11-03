import joblib
import pandas as pd
from generate_datasets import generate_datasets
from pathlib import Path
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)


def load_clean_dataset(filename, required_columns):
    path = DATASET_DIR / filename
    if not path.exists() or path.stat().st_size == 0:
        generate_datasets()
    frame = pd.read_csv(path)
    missing = set(required_columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{filename} is missing columns: {', '.join(sorted(missing))}")
    frame = frame[required_columns].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < 10:
        raise ValueError(f"{filename} needs at least 10 valid rows; found {len(frame)}")
    return frame


def train_eta_model():
    columns = ["distance_km", "hour_of_day", "day_of_week", "traffic_index", "route_id", "eta_minutes"]
    df = load_clean_dataset("eta_data.csv", columns)

    X = df[["distance_km", "hour_of_day", "day_of_week", "traffic_index", "route_id"]]
    y = df["eta_minutes"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = RandomForestRegressor(n_estimators=250, random_state=42)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    r2 = r2_score(y_test, preds)

    joblib.dump(model, MODEL_DIR / "eta_model.pkl")
    print(f"ETA model trained. MAE={mae:.2f}, R2={r2:.3f}")


def train_demand_model():
    columns = ["stop_id", "hour_of_day", "day_of_week", "route_id", "is_weekend", "passenger_count"]
    df = load_clean_dataset("demand_data.csv", columns)

    X = df[["stop_id", "hour_of_day", "day_of_week", "route_id", "is_weekend"]]
    y = df["passenger_count"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = RandomForestRegressor(n_estimators=250, random_state=42)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    r2 = r2_score(y_test, preds)

    joblib.dump(model, MODEL_DIR / "demand_model.pkl")
    print(f"Demand model trained. MAE={mae:.2f}, R2={r2:.3f}")


if __name__ == "__main__":
    if any(not (DATASET_DIR / name).exists() or (DATASET_DIR / name).stat().st_size == 0 for name in ("eta_data.csv", "demand_data.csv")):
        generate_datasets()
    train_eta_model()
    train_demand_model()
    print("Synthetic training data was used to generate the ML model files. ")
    print("These models are demo/synthetic models for local project demonstration.")