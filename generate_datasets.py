import csv
import random
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
RANDOM_SEED = 42


def write_eta_dataset(path, rows=1500):
    randomizer = random.Random(RANDOM_SEED)
    fields = ["distance_km", "hour_of_day", "day_of_week", "traffic_index", "route_id", "eta_minutes"]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for _ in range(rows):
            distance = randomizer.uniform(0.3, 18.0)
            hour = randomizer.randrange(24)
            day = randomizer.randrange(7)
            traffic = randomizer.uniform(0.1, 1.0)
            route_id = randomizer.randint(1, 12)
            speed = max(10.0, 31.0 - (traffic * 15.0) - (8.0 if hour in (8, 9, 17, 18, 19) else 0.0))
            eta = (distance / speed) * 60.0 + randomizer.uniform(0.5, 4.0)
            writer.writerow({
                "distance_km": round(distance, 3),
                "hour_of_day": hour,
                "day_of_week": day,
                "traffic_index": round(traffic, 3),
                "route_id": route_id,
                "eta_minutes": round(eta, 2),
            })


def write_demand_dataset(path, rows=1500):
    randomizer = random.Random(RANDOM_SEED + 1)
    fields = ["stop_id", "hour_of_day", "day_of_week", "route_id", "is_weekend", "passenger_count"]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for _ in range(rows):
            stop_id = randomizer.randint(1, 80)
            hour = randomizer.randrange(24)
            day = randomizer.randrange(7)
            route_id = randomizer.randint(1, 12)
            is_weekend = int(day >= 5)
            commute_peak = 1.7 if hour in (8, 9, 17, 18, 19) else 1.0
            mean_demand = (4.0 + (stop_id % 9) + (3.0 * is_weekend)) * commute_peak
            passengers = max(0, int(randomizer.gauss(mean_demand, max(mean_demand * 0.25, 1.0))))
            writer.writerow({
                "stop_id": stop_id,
                "hour_of_day": hour,
                "day_of_week": day,
                "route_id": route_id,
                "is_weekend": is_weekend,
                "passenger_count": passengers,
            })


def generate_datasets():
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    write_eta_dataset(DATASET_DIR / "eta_data.csv")
    write_demand_dataset(DATASET_DIR / "demand_data.csv")
    print(f"Generated synthetic datasets in {DATASET_DIR}")


if __name__ == "__main__":
    generate_datasets()