import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")

    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_NAME = os.getenv("DB_NAME", "autonex")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    USE_SQLITE_FALLBACK = os.getenv("USE_SQLITE_FALLBACK", "1") == "1"
    DB_CONNECTION_TIMEOUT_SECONDS = float(os.getenv("DB_CONNECTION_TIMEOUT_SECONDS", "2"))
    GEOCODE_TIMEOUT_SECONDS = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "8"))
    MAX_LOCATION_LENGTH = int(os.getenv("MAX_LOCATION_LENGTH", "250"))
    MIN_TRIP_DISTANCE_KM = float(os.getenv("MIN_TRIP_DISTANCE_KM", "0.05"))
    DEFAULT_SPEED_KMH = float(os.getenv("DEFAULT_SPEED_KMH", "18"))
    DEMO_GPS_INTERVAL_SECONDS = float(os.getenv("DEMO_GPS_INTERVAL_SECONDS", "3"))
