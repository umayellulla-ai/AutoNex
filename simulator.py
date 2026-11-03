import threading
from datetime import datetime

from db import db_execute
from services.geo import distance_along_route_km


_SIMULATORS = {}
_SIMULATOR_LOCK = threading.Lock()


def _record_demo_position(trip_id, stop, stops, speed_kmh):
    latitude = float(stop["latitude"])
    longitude = float(stop["longitude"])
    progress_km = distance_along_route_km(latitude, longitude, stops)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_execute(
        "UPDATE trips SET current_lat = ?, current_lng = ?, current_speed_kmh = ?, "
        "progress_km = ?, last_update = ? WHERE trip_id = ? AND status = 'ACTIVE'",
        (latitude, longitude, speed_kmh, progress_km, timestamp, trip_id),
    )
    db_execute(
        "INSERT INTO locations (trip_id, latitude, longitude, speed_kmh, source) "
        "VALUES (?, ?, ?, ?, 'DEMO')",
        (trip_id, latitude, longitude, speed_kmh),
    )


def start_demo_simulator(trip_id, route_stops, interval_seconds=3.0, speed_kmh=18.0):
    stops = list(route_stops)
    if not stops:
        return False

    with _SIMULATOR_LOCK:
        existing = _SIMULATORS.get(trip_id)
        if existing and existing["thread"].is_alive():
            return False
        stop_event = threading.Event()

        def run():
            index = 0
            while not stop_event.is_set():
                try:
                    _record_demo_position(trip_id, stops[index], stops, speed_kmh)
                except Exception:
                    break
                index = (index + 1) % len(stops)
                if stop_event.wait(interval_seconds):
                    break

        worker = threading.Thread(target=run, name=f"autonex-demo-{trip_id}", daemon=True)
        _SIMULATORS[trip_id] = {"event": stop_event, "thread": worker}
        worker.start()
    return True


def stop_demo_simulator(trip_id):
    with _SIMULATOR_LOCK:
        simulator = _SIMULATORS.pop(trip_id, None)
    if not simulator:
        return False
    simulator["event"].set()
    return True