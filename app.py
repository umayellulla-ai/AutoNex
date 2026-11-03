import json
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from db import db_execute, db_fetch_all, db_fetch_one, get_db_connection
from services.demand import predict_demand
from services.eta import estimate_eta
from services.fare import calculate_fare, get_fare_settings
from services.geo import (
    distance_along_route_km,
    distance_between_route_stops,
    geocode_location,
    haversine_km,
    nearest_route_stop,
)
from services.seats import available_seats_for_trip
from services.simulator import start_demo_simulator, stop_demo_simulator

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
def ensure_sqlite_schema():
    conn = sqlite3.connect(str(BASE_DIR / "autonex.db"))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS drivers (
            driver_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            license_number TEXT NULL
        );

        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS routes (
            route_id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_name TEXT NOT NULL,
            source_name TEXT NOT NULL,
            destination_name TEXT NOT NULL,
            created_by_user_id INTEGER NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS route_stops (
            stop_id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL,
            stop_order INTEGER NOT NULL,
            stop_name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            distance_from_start_km REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS autos (
            auto_id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL UNIQUE,
            vehicle_no TEXT NOT NULL UNIQUE,
            capacity INTEGER NOT NULL,
            route_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trips (
            trip_id INTEGER PRIMARY KEY AUTOINCREMENT,
            auto_id INTEGER NOT NULL,
            route_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            gps_mode TEXT NOT NULL,
            walkin_seats INTEGER NOT NULL DEFAULT 0,
            current_lat REAL NULL,
            current_lng REAL NULL,
            current_speed_kmh REAL NULL,
            progress_km REAL NOT NULL DEFAULT 0,
            last_update DATETIME NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ended_at DATETIME NULL
        );

        CREATE TABLE IF NOT EXISTS locations (
            location_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            speed_kmh REAL NULL,
            source TEXT NOT NULL,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            passenger_id INTEGER NOT NULL,
            pickup_stop_id INTEGER NOT NULL,
            drop_stop_id INTEGER NOT NULL,
            booking_type TEXT NOT NULL DEFAULT 'INDIVIDUAL',
            distance_km REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'BOOKED',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            boarded_at DATETIME NULL,
            completed_at DATETIME NULL
        );

        CREATE TABLE IF NOT EXISTS fare_settings (
            setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_fare REAL NOT NULL,
            rate_per_km REAL NOT NULL,
            min_fare REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'INR',
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS fare_records (
            fare_id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL UNIQUE,
            distance_km REAL NOT NULL,
            base_fare REAL NOT NULL,
            rate_per_km REAL NOT NULL,
            total_fare REAL NOT NULL,
            calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS demand_data (
            demand_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_id INTEGER NOT NULL,
            observed_at DATETIME NOT NULL,
            hour_of_day INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            passenger_count INTEGER NOT NULL,
            data_source TEXT NOT NULL DEFAULT 'APP'
        );

        CREATE TABLE IF NOT EXISTS eta_predictions (
            prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            booking_id INTEGER NULL,
            stop_id INTEGER NOT NULL,
            distance_km REAL NOT NULL,
            formula_eta_min REAL NOT NULL,
            ml_eta_min REAL NULL,
            model_used TEXT NOT NULL,
            predicted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            notification_type TEXT NOT NULL DEFAULT 'UPDATES',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    booking_columns = {row[1] for row in conn.execute("PRAGMA table_info(bookings)")}
    if "booking_type" not in booking_columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN booking_type TEXT NOT NULL DEFAULT 'INDIVIDUAL'")
    conn.commit()
    conn.close()


def ensure_booking_type_schema():
    conn, db_type = get_db_connection()
    cursor = conn.cursor(dictionary=True) if db_type == "mysql" else conn.cursor()
    if db_type == "mysql":
        cursor.execute("SHOW COLUMNS FROM bookings LIKE 'booking_type'")
        column_exists = cursor.fetchone() is not None
        alter_query = "ALTER TABLE bookings ADD COLUMN booking_type ENUM('INDIVIDUAL','SHARED') NOT NULL DEFAULT 'INDIVIDUAL' AFTER drop_stop_id"
    else:
        cursor.execute("PRAGMA table_info(bookings)")
        column_exists = any(column[1] == "booking_type" for column in cursor.fetchall())
        alter_query = "ALTER TABLE bookings ADD COLUMN booking_type TEXT NOT NULL DEFAULT 'INDIVIDUAL'"
    if not column_exists:
        cursor.execute(alter_query)
    conn.commit()
    cursor.close()
    conn.close()


def ensure_seed_data():
    user_count = db_fetch_one("SELECT COUNT(*) AS total FROM users")
    if user_count and user_count["total"] > 0:
        route_count = db_fetch_one("SELECT COUNT(*) AS total FROM routes")
        if route_count and route_count["total"] > 0:
            return

    admin_hash = generate_password_hash("admin123")
    driver_hash = generate_password_hash("driver123")
    passenger_hash = generate_password_hash("pass123")

    if not db_fetch_one("SELECT * FROM users WHERE email = ?", ("admin@autonex.com",)):
        db_execute(
            "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Admin User", "admin@autonex.com", "9999999999", admin_hash, "ADMIN"),
        )
    if not db_fetch_one("SELECT * FROM users WHERE email = ?", ("driver@autonex.com",)):
        db_execute(
            "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Driver One", "driver@autonex.com", "8888888888", driver_hash, "DRIVER"),
        )
    if not db_fetch_one("SELECT * FROM users WHERE email = ?", ("passenger@autonex.com",)):
        db_execute(
            "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Passenger One", "passenger@autonex.com", "7777777777", passenger_hash, "PASSENGER"),
        )

    route_count = db_fetch_one("SELECT COUNT(*) AS total FROM routes")
    if route_count and route_count["total"] == 0:
        route_id = db_execute(
            "INSERT INTO routes (route_name, source_name, destination_name) VALUES (?, ?, ?)",
            ("City Loop", "Stop A", "Stop E"),
        )
        stops = [
            (1, "Stop A", 12.9716, 77.5946, 0.0),
            (2, "Stop B", 12.9740, 77.5980, 1.2),
            (3, "Stop C", 12.9775, 77.6010, 2.7),
            (4, "Stop D", 12.9810, 77.6050, 4.1),
            (5, "Stop E", 12.9845, 77.6100, 5.8),
        ]
        for stop_order, stop_name, lat, lng, dist in stops:
            db_execute(
                "INSERT INTO route_stops (route_id, stop_order, stop_name, latitude, longitude, distance_from_start_km) VALUES (?, ?, ?, ?, ?, ?)",
                (route_id, stop_order, stop_name, lat, lng, dist),
            )

    driver_user = db_fetch_one("SELECT * FROM users WHERE email = ?", ("driver@autonex.com",))
    driver_row = db_fetch_one("SELECT * FROM drivers WHERE user_id = ?", (driver_user["user_id"],))
    if driver_row is None:
        db_execute("INSERT INTO drivers (user_id, license_number) VALUES (?, ?)", (driver_user["user_id"], "DL-1001"))

    driver_entry = db_fetch_one("SELECT * FROM drivers WHERE user_id = ?", (driver_user["user_id"],))
    route_id = db_fetch_one("SELECT route_id FROM routes LIMIT 1")["route_id"]
    auto_count = db_fetch_one("SELECT COUNT(*) AS total FROM autos WHERE driver_id = ?", (driver_entry["driver_id"],))
    if auto_count and auto_count["total"] == 0:
        db_execute(
            "INSERT INTO autos (driver_id, vehicle_no, capacity, route_id) VALUES (?, ?, ?, ?)",
            (driver_entry["driver_id"], "KA01AB1234", 4, route_id),
        )

    passenger_user = db_fetch_one("SELECT * FROM users WHERE email = ?", ("passenger@autonex.com",))
    passenger_row = db_fetch_one("SELECT * FROM passengers WHERE user_id = ?", (passenger_user["user_id"],))
    if passenger_row is None:
        db_execute("INSERT INTO passengers (user_id) VALUES (?)", (passenger_user["user_id"],))

    if not db_fetch_one("SELECT * FROM fare_settings LIMIT 1"):
        db_execute(
            "INSERT INTO fare_settings (base_fare, rate_per_km, min_fare, currency, is_active) VALUES (?, ?, ?, ?, ?)",
            (25.00, 12.00, 25.00, "INR", 1),
        )

    if not db_fetch_one("SELECT * FROM trips LIMIT 1"):
        auto = db_fetch_one("SELECT * FROM autos LIMIT 1")
        route = db_fetch_one("SELECT * FROM routes LIMIT 1")
        db_execute(
            "INSERT INTO trips (auto_id, route_id, status, gps_mode, current_lat, current_lng, current_speed_kmh, progress_km) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (auto["auto_id"], route["route_id"], "ACTIVE", "DEMO", 12.9716, 77.5946, Config.DEFAULT_SPEED_KMH, 0.0),
        )

    if not db_fetch_one("SELECT * FROM demand_data LIMIT 1"):
        for stop in db_fetch_all("SELECT * FROM route_stops WHERE route_id = ? ORDER BY stop_order", (route_id,)):
            for hour in [8, 10, 12, 15, 18, 20]:
                db_execute(
                    "INSERT INTO demand_data (stop_id, observed_at, hour_of_day, day_of_week, passenger_count, data_source) VALUES (?, ?, ?, ?, ?, ?)",
                    (stop["stop_id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), hour, 1, 8 + (stop["stop_order"] * 2), "SYNTHETIC"),
                )


def ensure_demo_fleet():
    route = db_fetch_one("SELECT * FROM routes ORDER BY route_id LIMIT 1")
    if not route:
        return
    stops = db_fetch_all(
        "SELECT * FROM route_stops WHERE route_id = ? ORDER BY stop_order",
        (route["route_id"],),
    )
    if not stops:
        return

    demo_drivers = [
        ("driver@autonex.com", "Driver One", "8888888888", "DL-1001", "KA01AB1234"),
        ("driver2@autonex.com", "Driver Two", "8888888887", "DL-1002", "KA03CD5678"),
        ("driver3@autonex.com", "Driver Three", "8888888886", "DL-1003", "KA05EF9012"),
    ]
    for fleet_index, (email, name, phone, license_number, vehicle_no) in enumerate(demo_drivers):
        user = db_fetch_one("SELECT * FROM users WHERE email = ?", (email,))
        if not user:
            user_id = db_execute(
                "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                (name, email, phone, generate_password_hash("driver123"), "DRIVER"),
            )
        else:
            user_id = user["user_id"]

        driver = db_fetch_one("SELECT * FROM drivers WHERE user_id = ?", (user_id,))
        if not driver:
            driver_id = db_execute(
                "INSERT INTO drivers (user_id, license_number) VALUES (?, ?)",
                (user_id, license_number),
            )
        else:
            driver_id = driver["driver_id"]

        auto = db_fetch_one("SELECT * FROM autos WHERE vehicle_no = ?", (vehicle_no,))
        if not auto:
            auto = db_fetch_one("SELECT * FROM autos WHERE driver_id = ?", (driver_id,))
        if not auto:
            auto_id = db_execute(
                "INSERT INTO autos (driver_id, vehicle_no, capacity, route_id) VALUES (?, ?, ?, ?)",
                (driver_id, vehicle_no, 4, route["route_id"]),
            )
        else:
            auto_id = auto["auto_id"]

        active_trip = get_active_trip_for_auto(auto_id)
        if not active_trip:
            start = stops[fleet_index % len(stops)]
            db_execute(
                "INSERT INTO trips (auto_id, route_id, status, gps_mode, current_lat, current_lng, current_speed_kmh, progress_km) "
                "VALUES (?, ?, 'ACTIVE', 'DEMO', ?, ?, ?, 0)",
                (auto_id, route["route_id"], float(start["latitude"]), float(start["longitude"]), Config.DEFAULT_SPEED_KMH),
            )
        elif active_trip["gps_mode"] == "DEMO" and active_trip["last_update"] is None:
            start = stops[fleet_index % len(stops)]
            db_execute(
                "UPDATE trips SET current_lat = ?, current_lng = ?, current_speed_kmh = ?, progress_km = ? WHERE trip_id = ?",
                (float(start["latitude"]), float(start["longitude"]), Config.DEFAULT_SPEED_KMH,
                 float(start["distance_from_start_km"]), active_trip["trip_id"]),
            )


def get_active_trip_for_auto(auto_id):
    return db_fetch_one(
        "SELECT * FROM trips WHERE auto_id = ? AND status = 'ACTIVE' ORDER BY trip_id DESC LIMIT 1",
        (auto_id,),
    )


def route_stop_summary(route_id):
    stops = db_fetch_all(
        "SELECT * FROM route_stops WHERE route_id = ? ORDER BY stop_order ASC",
        (route_id,),
    )
    return stops


def create_notification(user_id, title, body, notification_type="UPDATES"):
    """Create a notification only when the same event is not already present."""
    existing = db_fetch_one(
        "SELECT notification_id FROM notifications WHERE user_id = ? AND title = ? AND body = ? LIMIT 1",
        (user_id, title, body),
    )
    if existing:
        return existing["notification_id"]
    return db_execute(
        "INSERT INTO notifications (user_id, title, body, notification_type) VALUES (?, ?, ?, ?)",
        (user_id, title, body, notification_type),
    )


def passenger_profile(user_id):
    return db_fetch_one(
        "SELECT u.user_id, u.full_name, u.email, u.phone, u.role, p.passenger_id "
        "FROM users u JOIN passengers p ON p.user_id = u.user_id WHERE u.user_id = ?",
        (user_id,),
    )


def build_booking_quote(auto_id, pickup_address, drop_address, booking_type):
    if not auto_id or not pickup_address or not drop_address:
        return None, "Enter a pickup location and destination.", 400
    if len(pickup_address) > Config.MAX_LOCATION_LENGTH or len(drop_address) > Config.MAX_LOCATION_LENGTH:
        return None, f"Locations must be {Config.MAX_LOCATION_LENGTH} characters or fewer.", 400
    if booking_type not in {"INDIVIDUAL", "SHARED"}:
        return None, "Choose Individual or Shared booking.", 400

    trip = get_active_trip_for_auto(auto_id)
    if not trip:
        return None, "Auto is not active right now.", 400
    stops = route_stop_summary(trip["route_id"])
    if not stops:
        return None, "This auto has no route stops configured.", 400

    pickup_location = geocode_location(pickup_address)
    drop_location = geocode_location(drop_address)
    if not pickup_location or not drop_location:
        return None, "Could not find one of those locations. Try a more complete address.", 400

    pickup = nearest_route_stop(pickup_location["latitude"], pickup_location["longitude"], stops)
    drop = nearest_route_stop(drop_location["latitude"], drop_location["longitude"], stops)
    distance_km = haversine_km(
        pickup_location["latitude"], pickup_location["longitude"],
        drop_location["latitude"], drop_location["longitude"],
    )
    if distance_km < Config.MIN_TRIP_DISTANCE_KM:
        return None, "Pickup and destination must be different locations.", 400
    same_route_stop = pickup["stop_id"] == drop["stop_id"]
    segment_seats = 0 if same_route_stop else available_seats_for_trip(
        trip["trip_id"], pickup["stop_id"], drop["stop_id"]
    )
    booking_warning = None
    if same_route_stop:
        booking_warning = "These locations map to the same configured route stop. This fare is an estimate only; this auto cannot book the trip."
    elif segment_seats <= 0:
        booking_warning = "No seats are available for this route segment. This fare is an estimate only."

    settings = get_fare_settings()
    total_fare = round(calculate_fare(distance_km, settings), 2)
    eta = estimate_eta(
        distance_km,
        trip["route_id"],
        trip["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH,
    )
    matching_riders = db_fetch_one(
        "SELECT COUNT(*) AS total FROM bookings WHERE trip_id = ? AND pickup_stop_id = ? "
        "AND drop_stop_id = ? AND status IN ('BOOKED','ONBOARD')",
        (trip["trip_id"], pickup["stop_id"], drop["stop_id"]),
    )

    return {
        "auto_id": auto_id,
        "trip_id": trip["trip_id"],
        "pickup_stop_id": int(pickup["stop_id"]),
        "drop_stop_id": int(drop["stop_id"]),
        "distance_km": round(distance_km, 2),
        "booking_type": booking_type,
        "fare": total_fare,
        "total_fare": total_fare,
        "shared_passengers": int(matching_riders["total"]) + 1 if booking_type == "SHARED" else None,
        "available_seats": segment_seats,
        "eta_minutes": eta["ml_eta_min"],
        "can_book": not booking_warning,
        "booking_warning": booking_warning,
        "base_fare": float(settings["base_fare"]),
        "rate_per_km": float(settings["rate_per_km"]),
    }, None, 200


def get_driver_auto_for_user(user_id):
    driver = db_fetch_one("SELECT * FROM drivers WHERE user_id = ?", (user_id,))
    if not driver:
        return None
    return db_fetch_one("SELECT * FROM autos WHERE driver_id = ?", (driver["driver_id"],))


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    app.secret_key = app.config["SECRET_KEY"]

    ensure_sqlite_schema()
    ensure_booking_type_schema()
    ensure_seed_data()
    ensure_demo_fleet()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not email or not password:
                return render_template("login.html", error="Email and password are required.")
            user = db_fetch_one("SELECT * FROM users WHERE email = ?", (email,))
            if not user or not check_password_hash(user["password_hash"], password):
                return render_template("login.html", error="Invalid email or password.")
            session["user_id"] = user["user_id"]
            session["role"] = user["role"].upper()
            session["user_email"] = user["email"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            role = request.form.get("role", "PASSENGER").upper()
            password = request.form.get("password", "")
            if not full_name or not email or not phone or not password:
                return render_template("register.html", error="Please complete all fields.")
            if db_fetch_one("SELECT * FROM users WHERE email = ?", (email,)):
                return render_template("register.html", error="This email is already registered.")
            user_id = db_execute(
                "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                (full_name, email, phone, generate_password_hash(password), role),
            )
            if role == "DRIVER":
                db_execute("INSERT INTO drivers (user_id, license_number) VALUES (?, ?)", (user_id, "DL-NEW"))
            elif role == "PASSENGER":
                db_execute("INSERT INTO passengers (user_id) VALUES (?)", (user_id,))
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/dashboard")
    def dashboard():
        role = (session.get("role") or "PASSENGER").upper()
        if role == "DRIVER":
            return render_template("driver_dashboard.html", user=session.get("full_name"))
        if role == "ADMIN":
            return render_template("admin_dashboard.html", user=session.get("full_name"))
        profile = passenger_profile(session.get("user_id"))
        return render_template("passenger_dashboard.html", user=session.get("full_name"), profile=profile)

    @app.route("/driver")
    def driver_dashboard_route():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return render_template("driver_dashboard.html", user=session.get("full_name"))

    @app.route("/passenger")
    def passenger_dashboard_route():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        profile = passenger_profile(session.get("user_id"))
        return render_template("passenger_dashboard.html", user=session.get("full_name"), profile=profile)

    @app.route("/admin")
    def admin_dashboard_route():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return render_template("admin_dashboard.html", user=session.get("full_name"))

    @app.route("/trip")
    def trip_page():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return render_template("trip.html", user=session.get("full_name"))

    @app.route("/booking")
    def booking_page():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return render_template(
            "booking.html",
            user=session.get("full_name"),
            auto_id=request.args.get("auto_id", ""),
            pickup_location=request.args.get("pickup_location", ""),
            drop_location=request.args.get("drop_location", ""),
            booking_type=request.args.get("booking_type", "INDIVIDUAL"),
        )

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/api/login", methods=["POST"])
    def api_login():
        data = request.get_json(silent=True) or request.form
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required."}), 400
        user = db_fetch_one("SELECT * FROM users WHERE email = ?", (email,))
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"success": False, "error": "Invalid email or password."}), 401
        session["user_id"] = user["user_id"]
        session["role"] = user["role"].upper()
        session["full_name"] = user["full_name"]
        session["user_email"] = user["email"]
        return jsonify({"success": True, "role": user["role"].upper(), "redirect": "/dashboard"})

    @app.route("/api/register", methods=["POST"])
    def api_register():
        data = request.get_json(silent=True) or request.form
        full_name = str(data.get("full_name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        phone = str(data.get("phone", "")).strip()
        role = str(data.get("role", "PASSENGER")).upper()
        password = str(data.get("password", ""))
        if not full_name or not email or not phone or not password:
            return jsonify({"success": False, "error": "Please complete all fields."}), 400
        if db_fetch_one("SELECT * FROM users WHERE email = ?", (email,)):
            return jsonify({"success": False, "error": "Email already registered."}), 409
        user_id = db_execute(
            "INSERT INTO users (full_name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (full_name, email, phone, generate_password_hash(password), role),
        )
        if role == "DRIVER":
            db_execute("INSERT INTO drivers (user_id, license_number) VALUES (?, ?)", (user_id, "DL-NEW"))
        elif role == "PASSENGER":
            db_execute("INSERT INTO passengers (user_id) VALUES (?)", (user_id,))
        return jsonify({"success": True, "message": "Registration successful."})

    @app.route("/api/admin/summary")
    def admin_summary():
        active_trips = db_fetch_one("SELECT COUNT(*) AS total FROM trips WHERE status = 'ACTIVE'")
        drivers = db_fetch_one("SELECT COUNT(*) AS total FROM drivers")
        passengers = db_fetch_one("SELECT COUNT(*) AS total FROM passengers")
        autos = db_fetch_one("SELECT COUNT(*) AS total FROM autos")
        return jsonify(
            {
                "drivers": drivers["total"],
                "passengers": passengers["total"],
                "autos": autos["total"],
                "active_trips": active_trips["total"],
            }
        )

    @app.route("/api/autos/nearby")
    def autos_nearby():
        active_trips = db_fetch_all("SELECT * FROM trips WHERE status = 'ACTIVE' ORDER BY trip_id")
        result = []
        for trip in active_trips:
            auto = db_fetch_one("SELECT * FROM autos WHERE auto_id = ?", (trip["auto_id"],))
            driver = db_fetch_one("SELECT * FROM drivers WHERE driver_id = ?", (auto["driver_id"],))
            user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (driver["user_id"],))
            route = db_fetch_one("SELECT * FROM routes WHERE route_id = ?", (trip["route_id"],))
            stops = route_stop_summary(trip["route_id"])
            current_lat = trip["current_lat"] if trip["current_lat"] is not None else stops[0]["latitude"]
            current_lng = trip["current_lng"] if trip["current_lng"] is not None else stops[0]["longitude"]
            current_progress = distance_along_route_km(current_lat, current_lng, stops)
            remaining_km = max(float(stops[-1]["distance_from_start_km"]) - current_progress, 0.0)
            eta = estimate_eta(
                remaining_km,
                trip["route_id"],
                trip["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH,
            )
            result.append(
                {
                    "trip_id": trip["trip_id"],
                    "auto_id": auto["auto_id"],
                    "vehicle_no": auto["vehicle_no"],
                    "capacity": auto["capacity"],
                    "available_seats": available_seats_for_trip(trip["trip_id"]),
                    "driver_name": user["full_name"],
                    "route_name": route["route_name"],
                    "route": route["source_name"] + " → " + route["destination_name"],
                    "current_lat": trip["current_lat"],
                    "current_lng": trip["current_lng"],
                    "current_speed_kmh": trip["current_speed_kmh"],
                    "last_update": trip["last_update"],
                    "progress_km": trip["progress_km"],
                    "gps_mode": trip["gps_mode"],
                    "eta_minutes": eta["ml_eta_min"],
                    "stops": stops,
                }
            )
        return jsonify({"autos": result})

    @app.route("/api/autos/<int:auto_id>/start-demo", methods=["POST"])
    def start_selected_auto_demo(auto_id):
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        auto = db_fetch_one("SELECT * FROM autos WHERE auto_id = ?", (auto_id,))
        if not auto:
            return jsonify({"success": False, "error": "Auto not found."}), 404
        trip = get_active_trip_for_auto(auto_id)
        if not trip:
            return jsonify({"success": False, "error": "Auto has no active trip."}), 409
        if trip["gps_mode"] != "DEMO":
            return jsonify({"success": False, "error": "This auto is using REAL GPS, not DEMO GPS."}), 409
        started = start_demo_simulator(
            trip["trip_id"],
            route_stop_summary(trip["route_id"]),
            Config.DEMO_GPS_INTERVAL_SECONDS,
            Config.DEFAULT_SPEED_KMH,
        )
        return jsonify({
            "success": True,
            "auto_id": auto_id,
            "trip_id": trip["trip_id"],
            "gps_mode": "DEMO",
            "started": started,
            "message": "DEMO GPS MODE active.",
        })

    @app.route("/api/driver/start-trip", methods=["POST"])
    def driver_start_trip():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        user_id = session["user_id"]
        auto = get_driver_auto_for_user(user_id)
        if not auto:
            return jsonify({"success": False, "error": "No auto linked to this driver."}), 400
        route = db_fetch_one("SELECT * FROM routes WHERE route_id = ?", (auto["route_id"],))
        trip = get_active_trip_for_auto(auto["auto_id"])
        if trip:
                return jsonify({"success": True, "trip_id": trip["trip_id"], "auto_id": auto["auto_id"], "mode": trip["gps_mode"], "message": "Trip already active."})
        trip_id = db_execute(
            "INSERT INTO trips (auto_id, route_id, status, gps_mode, current_lat, current_lng, current_speed_kmh, progress_km) VALUES (?, ?, 'ACTIVE', 'REAL', ?, ?, ?, 0)",
            (auto["auto_id"], auto["route_id"], 0.0, 0.0, Config.DEFAULT_SPEED_KMH),
        )
        return jsonify({"success": True, "trip_id": trip_id, "auto_id": auto["auto_id"], "mode": "REAL", "route": route["route_name"]})

    @app.route("/api/driver/start-demo-trip", methods=["POST"])
    def driver_start_demo_trip():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        auto = get_driver_auto_for_user(session["user_id"])
        if not auto:
            return jsonify({"success": False, "error": "No auto linked to this driver."}), 400
        trip = get_active_trip_for_auto(auto["auto_id"])
        if trip:
            if trip["gps_mode"] != "DEMO":
                return jsonify({"success": False, "error": "A REAL GPS trip is already active."}), 409
            start_demo_simulator(
                trip["trip_id"],
                route_stop_summary(trip["route_id"]),
                Config.DEMO_GPS_INTERVAL_SECONDS,
                Config.DEFAULT_SPEED_KMH,
            )
            return jsonify({"success": True, "trip_id": trip["trip_id"], "auto_id": auto["auto_id"], "mode": "DEMO", "message": "Demo trip already running."})
        route = route_stop_summary(auto["route_id"])
        if not route:
            return jsonify({"success": False, "error": "No route stops are configured."}), 400
        start_stop = route[0]
        trip_id = db_execute(
            "INSERT INTO trips (auto_id, route_id, status, gps_mode, current_lat, current_lng, current_speed_kmh, progress_km) VALUES (?, ?, 'ACTIVE', 'DEMO', ?, ?, ?, 0)",
            (auto["auto_id"], auto["route_id"], float(start_stop["latitude"]), float(start_stop["longitude"]), Config.DEFAULT_SPEED_KMH),
        )
        start_demo_simulator(
            trip_id, route, Config.DEMO_GPS_INTERVAL_SECONDS, Config.DEFAULT_SPEED_KMH
        )
        return jsonify({"success": True, "trip_id": trip_id, "auto_id": auto["auto_id"], "mode": "DEMO", "message": "Demo trip started."})

    @app.route("/api/driver/update-location", methods=["POST"])
    def driver_update_location():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        auto = get_driver_auto_for_user(session["user_id"])
        if not auto:
            return jsonify({"success": False, "error": "No auto registered for this driver."}), 404
        trip = get_active_trip_for_auto(auto["auto_id"])
        if not trip:
            return jsonify({"success": False, "error": "No active trip available."}), 400
        data = request.get_json(silent=True) or request.form
        lat = float(data.get("lat", 0))
        lng = float(data.get("lng", 0))
        speed = float(data.get("speed", Config.DEFAULT_SPEED_KMH))
        if lat == 0 and lng == 0:
            return jsonify({"success": False, "error": "Invalid GPS coordinates."}), 400
        route = route_stop_summary(trip["route_id"])
        progress_km = distance_along_route_km(lat, lng, route)
        db_execute(
            "UPDATE trips SET current_lat = ?, current_lng = ?, current_speed_kmh = ?, progress_km = ?, last_update = ? WHERE trip_id = ?",
            (lat, lng, speed, progress_km, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trip["trip_id"]),
        )
        db_execute(
            "INSERT INTO locations (trip_id, latitude, longitude, speed_kmh, source) VALUES (?, ?, ?, ?, ?)",
            (trip["trip_id"], lat, lng, speed, "REAL"),
        )
        return jsonify({"success": True, "trip_id": trip["trip_id"], "lat": lat, "lng": lng})

    @app.route("/api/driver/end-trip", methods=["POST"])
    def driver_end_trip():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        auto = get_driver_auto_for_user(session["user_id"])
        if not auto:
            return jsonify({"success": False, "error": "No auto registered for this driver."}), 404
        trip = get_active_trip_for_auto(auto["auto_id"])
        if not trip:
            return jsonify({"success": False, "error": "No active trip to end."}), 400
        stop_demo_simulator(trip["trip_id"])
        db_execute(
            "UPDATE trips SET status = 'ENDED', ended_at = ? WHERE trip_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trip["trip_id"]),
        )
        return jsonify({"success": True, "trip_id": trip["trip_id"], "message": "Trip ended."})

    @app.route("/api/driver/demo-location", methods=["POST"])
    def driver_demo_location():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        auto = get_driver_auto_for_user(session["user_id"])
        if not auto:
            return jsonify({"success": False, "error": "No auto registered for this driver."}), 404
        trip = get_active_trip_for_auto(auto["auto_id"])
        if not trip:
            return jsonify({"success": False, "error": "No active trip available."}), 400
        data = request.get_json(silent=True) or request.form
        route = route_stop_summary(auto["route_id"])
        point_index = int(data.get("index", 0))
        if point_index >= len(route):
            point_index = 0
        point = route[point_index]
        db_execute(
            "UPDATE trips SET current_lat = ?, current_lng = ?, current_speed_kmh = ?, last_update = ? WHERE trip_id = ?",
                    (point["latitude"], point["longitude"], Config.DEFAULT_SPEED_KMH, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trip["trip_id"]),
        )
        db_execute(
            "INSERT INTO locations (trip_id, latitude, longitude, speed_kmh, source) VALUES (?, ?, ?, ?, ?)",
                    (trip["trip_id"], point["latitude"], point["longitude"], Config.DEFAULT_SPEED_KMH, "DEMO"),
        )
        return jsonify({"success": True, "lat": point["latitude"], "lng": point["longitude"], "index": point_index})

    @app.route("/api/booking/quote", methods=["POST"])
    def booking_quote():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        data = request.get_json(silent=True) or request.form
        try:
            auto_id = int(data.get("auto_id", 0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Choose a valid auto."}), 400
        pickup_address = str(data.get("pickup_location", "")).strip()
        drop_address = str(data.get("drop_location", "")).strip()
        booking_type = str(data.get("booking_type", "INDIVIDUAL")).strip().upper()
        passenger = db_fetch_one("SELECT * FROM passengers WHERE user_id = ?", (session["user_id"],))
        if not passenger:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        try:
            quote, error, status = build_booking_quote(auto_id, pickup_address, drop_address, booking_type)
        except (OSError, TimeoutError, json.JSONDecodeError):
            return jsonify({"success": False, "error": "Address lookup is temporarily unavailable. Please try again."}), 503
        if error:
            return jsonify({"success": False, "error": error}), status
        return jsonify({"success": True, **quote})

    @app.route("/api/passenger/overview")
    def passenger_overview():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        profile = passenger_profile(session["user_id"])
        if not profile:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        fare_settings = get_fare_settings()
        completed = db_fetch_all(
            "SELECT b.booking_id, b.trip_id, b.distance_km, b.created_at, b.completed_at, "
            "pickup.stop_name AS pickup_name, dropoff.stop_name AS drop_name, "
            "fr.total_fare, fr.base_fare, fr.rate_per_km "
            "FROM bookings b JOIN route_stops pickup ON pickup.stop_id = b.pickup_stop_id "
            "JOIN route_stops dropoff ON dropoff.stop_id = b.drop_stop_id "
            "LEFT JOIN fare_records fr ON fr.booking_id = b.booking_id "
            "WHERE b.passenger_id = ? AND b.status = 'COMPLETED' ORDER BY b.completed_at DESC, b.booking_id DESC",
            (profile["passenger_id"],),
        )
        frequent = db_fetch_all(
            "SELECT pickup.stop_name AS pickup_name, dropoff.stop_name AS drop_name, COUNT(*) AS total "
            "FROM bookings b JOIN route_stops pickup ON pickup.stop_id = b.pickup_stop_id "
            "JOIN route_stops dropoff ON dropoff.stop_id = b.drop_stop_id "
            "WHERE b.passenger_id = ? AND b.status = 'COMPLETED' "
            "GROUP BY b.pickup_stop_id, b.drop_stop_id, pickup.stop_name, dropoff.stop_name "
            "ORDER BY total DESC LIMIT 4",
            (profile["passenger_id"],),
        )
        active = db_fetch_one(
            "SELECT b.booking_id, b.trip_id, b.status AS booking_status, b.booking_type, b.distance_km, "
            "pickup.stop_name AS pickup_name, dropoff.stop_name AS drop_name, "
            "a.vehicle_no, u.full_name AS driver_name, u.phone AS driver_phone, "
            "t.current_lat, t.current_lng, t.current_speed_kmh, t.route_id, fr.total_fare "
            "FROM bookings b JOIN trips t ON t.trip_id = b.trip_id JOIN autos a ON a.auto_id = t.auto_id "
            "JOIN drivers d ON d.driver_id = a.driver_id JOIN users u ON u.user_id = d.user_id "
            "JOIN route_stops pickup ON pickup.stop_id = b.pickup_stop_id "
            "JOIN route_stops dropoff ON dropoff.stop_id = b.drop_stop_id "
            "LEFT JOIN fare_records fr ON fr.booking_id = b.booking_id "
            "WHERE b.passenger_id = ? AND b.status IN ('BOOKED','ONBOARD') ORDER BY b.booking_id DESC LIMIT 1",
            (profile["passenger_id"],),
        )
        active_payload = None
        if active:
            active_payload = dict(active)
            active_payload["stops"] = route_stop_summary(active["route_id"])
            route = active_payload["stops"]
            pickup_stop = next((stop for stop in route if stop["stop_name"] == active["pickup_name"]), route[0] if route else None)
            destination = next((stop for stop in route if stop["stop_name"] == active["drop_name"]), route[-1] if route else None)
            if pickup_stop and destination:
                remaining = max(float(destination["distance_from_start_km"]) - distance_along_route_km(
                    active["current_lat"] or pickup_stop["latitude"], active["current_lng"] or pickup_stop["longitude"], route
                ), 0.0)
                active_payload["eta_minutes"] = estimate_eta(
                    remaining, active["route_id"], active["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH
                )["ml_eta_min"]
                if active_payload["eta_minutes"] is not None and active_payload["eta_minutes"] <= 2:
                    create_notification(session["user_id"], "Your auto is nearly here", "Your booked auto is about 2 minutes away.", "UPDATES")
        return jsonify({
            "success": True,
            "profile": profile,
            "fare_settings": fare_settings,
            "active_booking": active_payload,
            "completed_bookings": completed,
            "frequent_routes": frequent,
        })

    @app.route("/api/passenger/notifications")
    def passenger_notifications():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        notifications = db_fetch_all(
            "SELECT notification_id, title, body, notification_type, is_read, created_at "
            "FROM notifications WHERE user_id = ? ORDER BY created_at DESC, notification_id DESC LIMIT 50",
            (session["user_id"],),
        )
        return jsonify({"success": True, "notifications": notifications})

    @app.route("/api/passenger/notifications/<int:notification_id>/read", methods=["POST"])
    def mark_notification_read(notification_id):
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        db_execute(
            "UPDATE notifications SET is_read = 1 WHERE notification_id = ? AND user_id = ?",
            (notification_id, session["user_id"]),
        )
        return jsonify({"success": True})

    @app.route("/api/passenger/booking-options")
    def passenger_booking_options():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        stops = db_fetch_all("SELECT * FROM route_stops ORDER BY route_id, stop_order")
        autos_response = db_fetch_all("SELECT auto_id FROM autos ORDER BY auto_id")
        autos = []
        for row in autos_response:
            auto = db_fetch_one("SELECT * FROM autos WHERE auto_id = ?", (row["auto_id"],))
            trip = get_active_trip_for_auto(auto["auto_id"])
            if trip:
                route = route_stop_summary(trip["route_id"])
                current_lat = trip["current_lat"] if trip["current_lat"] is not None else route[0]["latitude"]
                current_lng = trip["current_lng"] if trip["current_lng"] is not None else route[0]["longitude"]
                remaining_km = max(float(route[-1]["distance_from_start_km"]) - distance_along_route_km(current_lat, current_lng, route), 0.0)
                eta = estimate_eta(remaining_km, trip["route_id"], trip["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH)
                autos.append({"auto_id": auto["auto_id"], "vehicle_no": auto["vehicle_no"], "available_seats": available_seats_for_trip(trip["trip_id"]), "capacity": auto["capacity"], "eta_minutes": eta["ml_eta_min"]})
        return jsonify({"success": True, "stops": stops, "autos": autos})

    def build_stop_quote(auto_id, pickup_stop_id, drop_stop_id, booking_type):
        trip = get_active_trip_for_auto(auto_id)
        if not trip:
            return None, "Auto is not active right now.", 400
        stops = route_stop_summary(trip["route_id"])
        pickup = next((stop for stop in stops if int(stop["stop_id"]) == pickup_stop_id), None)
        drop = next((stop for stop in stops if int(stop["stop_id"]) == drop_stop_id), None)
        if not pickup or not drop or float(drop["distance_from_start_km"]) <= float(pickup["distance_from_start_km"]):
            return None, "Choose a destination after the pickup stop.", 400
        if booking_type not in {"INDIVIDUAL", "SHARED"}:
            return None, "Choose Individual or Shared booking.", 400
        distance_km = round(float(drop["distance_from_start_km"]) - float(pickup["distance_from_start_km"]), 3)
        settings = get_fare_settings()
        total_fare = round(calculate_fare(distance_km, settings), 2)
        eta = estimate_eta(distance_km, trip["route_id"], trip["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH)
        seats = available_seats_for_trip(trip["trip_id"], pickup_stop_id, drop_stop_id)
        return {
            "auto_id": auto_id, "trip_id": trip["trip_id"], "pickup_stop_id": pickup_stop_id,
            "drop_stop_id": drop_stop_id, "distance_km": distance_km, "booking_type": booking_type,
            "fare": total_fare, "total_fare": total_fare, "available_seats": seats,
            "capacity": db_fetch_one("SELECT capacity FROM autos WHERE auto_id = ?", (auto_id,))["capacity"],
            "eta_minutes": eta["ml_eta_min"], "base_fare": float(settings["base_fare"]),
            "rate_per_km": float(settings["rate_per_km"]), "can_book": seats > 0,
            "booking_warning": None if seats > 0 else "No seats are available for this route segment.",
        }, None, 200

    @app.route("/api/booking/quote-stops", methods=["POST"])
    def booking_quote_stops():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        data = request.get_json(silent=True) or request.form
        try:
            quote, error, status = build_stop_quote(int(data.get("auto_id")), int(data.get("pickup_stop_id")), int(data.get("drop_stop_id")), str(data.get("booking_type", "INDIVIDUAL")).upper())
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Choose pickup, destination, and auto."}), 400
        if error:
            return jsonify({"success": False, "error": error}), status
        return jsonify({"success": True, **quote})

    @app.route("/api/booking-stops", methods=["POST"])
    def create_stop_booking():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        passenger = passenger_profile(session["user_id"])
        if not passenger:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        existing = db_fetch_one("SELECT booking_id FROM bookings WHERE passenger_id = ? AND status IN ('BOOKED','ONBOARD')", (passenger["passenger_id"],))
        if existing:
            return jsonify({"success": False, "error": "You already have an upcoming trip."}), 409
        data = request.get_json(silent=True) or request.form
        try:
            auto_id = int(data.get("auto_id")); pickup_stop_id = int(data.get("pickup_stop_id")); drop_stop_id = int(data.get("drop_stop_id"))
            booking_type = str(data.get("booking_type", "INDIVIDUAL")).upper()
            quote, error, status = build_stop_quote(auto_id, pickup_stop_id, drop_stop_id, booking_type)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Choose pickup, destination, and auto."}), 400
        if error:
            return jsonify({"success": False, "error": error}), status
        if not quote["can_book"]:
            return jsonify({"success": False, "error": quote["booking_warning"]}), 409
        booking_id = db_execute(
            "INSERT INTO bookings (trip_id, passenger_id, pickup_stop_id, drop_stop_id, booking_type, distance_km, status) VALUES (?, ?, ?, ?, ?, ?, 'BOOKED')",
            (quote["trip_id"], passenger["passenger_id"], pickup_stop_id, drop_stop_id, booking_type, quote["distance_km"]),
        )
        db_execute("INSERT INTO fare_records (booking_id, distance_km, base_fare, rate_per_km, total_fare) VALUES (?, ?, ?, ?, ?)", (booking_id, quote["distance_km"], quote["base_fare"], quote["rate_per_km"], quote["total_fare"]))
        create_notification(session["user_id"], "Booking confirmed", f"Your AUTONEX auto is booked for {quote['total_fare']:.2f}.", "UPDATES")
        return jsonify({"success": True, "booking_id": booking_id, "fare": quote["total_fare"]})

    @app.route("/api/locations/resolve", methods=["POST"])
    def resolve_location():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        data = request.get_json(silent=True) or request.form
        address = str(data.get("address", "")).strip()
        if not address:
            return jsonify({"success": False, "error": "Enter a location to search."}), 400
        if len(address) > Config.MAX_LOCATION_LENGTH:
            return jsonify({"success": False, "error": f"Locations must be {Config.MAX_LOCATION_LENGTH} characters or fewer."}), 400
        try:
            location = geocode_location(address)
        except (OSError, TimeoutError, json.JSONDecodeError):
            return jsonify({"success": False, "error": "Address lookup is temporarily unavailable."}), 503
        if not location:
            return jsonify({"success": False, "error": "Location not found. Try a more complete address."}), 404
        return jsonify({"success": True, **location})

    @app.route("/api/booking", methods=["POST"])
    def create_booking():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please login first."}), 401
        data = request.get_json(silent=True) or request.form
        try:
            auto_id = int(data.get("auto_id", 0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Choose a valid auto."}), 400
        pickup_address = str(data.get("pickup_location", "")).strip()
        drop_address = str(data.get("drop_location", "")).strip()
        booking_type = str(data.get("booking_type", "INDIVIDUAL")).strip().upper()
        passenger = db_fetch_one("SELECT * FROM passengers WHERE user_id = ?", (session["user_id"],))
        if not passenger:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        existing = db_fetch_one(
            "SELECT * FROM bookings WHERE passenger_id = ? AND status IN ('BOOKED','ONBOARD') LIMIT 1",
            (passenger["passenger_id"],),
        )
        if existing:
            return jsonify({"success": False, "error": "Passenger already inside an active auto."}), 409
        try:
            quote, error, status = build_booking_quote(auto_id, pickup_address, drop_address, booking_type)
        except (OSError, TimeoutError, json.JSONDecodeError):
            return jsonify({"success": False, "error": "Address lookup is temporarily unavailable. Please try again."}), 503
        if error:
            return jsonify({"success": False, "error": error}), status
        if not quote["can_book"]:
            return jsonify({"success": False, "error": quote["booking_warning"], "estimate": quote}), 409

        booking_id = db_execute(
            "INSERT INTO bookings (trip_id, passenger_id, pickup_stop_id, drop_stop_id, booking_type, distance_km, status) VALUES (?, ?, ?, ?, ?, ?, 'BOOKED')",
            (quote["trip_id"], passenger["passenger_id"], quote["pickup_stop_id"], quote["drop_stop_id"], booking_type, quote["distance_km"]),
        )
        db_execute(
            "INSERT INTO fare_records (booking_id, distance_km, base_fare, rate_per_km, total_fare) VALUES (?, ?, ?, ?, ?)",
            (booking_id, quote["distance_km"], quote["base_fare"], quote["rate_per_km"], quote["total_fare"]),
        )
        create_notification(session["user_id"], "Booking confirmed", f"Your AUTONEX auto is booked for {quote['total_fare']:.2f}.", "UPDATES")
        fare_per_passenger = quote["total_fare"]
        shared_passengers = 1
        if booking_type == "SHARED":
            shared_passengers = quote["shared_passengers"]
        return jsonify(
            {
                "success": True,
                "booking_id": booking_id,
                "trip_id": quote["trip_id"],
                "booking_type": booking_type,
                "distance_km": quote["distance_km"],
                "fare": fare_per_passenger,
                "total_fare": quote["total_fare"],
                "shared_passengers": shared_passengers if booking_type == "SHARED" else None,
                "available_seats": available_seats_for_trip(
                    quote["trip_id"], quote["pickup_stop_id"], quote["drop_stop_id"]
                ),
            }
        )

    @app.route("/api/booking/<int:booking_id>/complete", methods=["POST"])
    def complete_booking(booking_id):
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        passenger = db_fetch_one("SELECT passenger_id FROM passengers WHERE user_id = ?", (session["user_id"],))
        if not passenger:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        booking = db_fetch_one("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,))
        if not booking:
            return jsonify({"success": False, "error": "Booking not found."}), 404
        if booking["passenger_id"] != passenger["passenger_id"]:
            return jsonify({"success": False, "error": "You can only complete your own ride."}), 403
        if booking["status"] not in {"BOOKED", "ONBOARD"}:
            return jsonify({"success": False, "error": "This ride is already complete."}), 409
        db_execute(
            "UPDATE bookings SET status = 'COMPLETED', completed_at = ? WHERE booking_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), booking_id),
        )
        fare = db_fetch_one("SELECT total_fare FROM fare_records WHERE booking_id = ?", (booking_id,))
        fare_text = f" Fare: {fare['total_fare']:.2f}." if fare else ""
        create_notification(session["user_id"], "Ride completed", f"Your AUTONEX ride is complete.{fare_text}", "UPDATES")
        create_notification(session["user_id"], "Seat released", "Your seat has been released for the next passenger.", "UPDATES")
        return jsonify({"success": True, "message": "Booking marked complete."})

    @app.route("/api/trip/current")
    def current_passenger_trip():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "Please log in first."}), 401
        passenger = db_fetch_one("SELECT passenger_id FROM passengers WHERE user_id = ?", (session["user_id"],))
        if not passenger:
            return jsonify({"success": False, "error": "Passenger account required."}), 403
        booking = db_fetch_one(
            "SELECT * FROM bookings WHERE passenger_id = ? AND status IN ('BOOKED','ONBOARD') ORDER BY booking_id DESC LIMIT 1",
            (passenger["passenger_id"],),
        )
        if not booking:
            return jsonify({"success": False, "error": "No active booking."}), 404
        trip = db_fetch_one("SELECT * FROM trips WHERE trip_id = ?", (booking["trip_id"],))
        auto = db_fetch_one("SELECT * FROM autos WHERE auto_id = ?", (trip["auto_id"],))
        fare = db_fetch_one("SELECT total_fare, base_fare, rate_per_km FROM fare_records WHERE booking_id = ?", (booking["booking_id"],))
        active_passengers = db_fetch_one(
            "SELECT COUNT(*) AS total FROM bookings WHERE trip_id = ? AND status IN ('BOOKED','ONBOARD')",
            (trip["trip_id"],),
        )
        return jsonify({
            "success": True,
            "trip_id": trip["trip_id"],
            "auto_id": auto["auto_id"],
            "vehicle_no": auto["vehicle_no"],
            "capacity": auto["capacity"],
            "gps_mode": trip["gps_mode"],
            "status": trip["status"],
            "booking_id": booking["booking_id"],
            "booking_status": booking["status"],
            "booking_type": booking["booking_type"],
            "pickup_stop_id": booking["pickup_stop_id"],
            "drop_stop_id": booking["drop_stop_id"],
            "distance_km": booking["distance_km"],
            "current_lat": trip["current_lat"],
            "current_lng": trip["current_lng"],
            "available_seats": available_seats_for_trip(
                trip["trip_id"], booking["pickup_stop_id"], booking["drop_stop_id"]
            ),
            "passengers": active_passengers["total"],
            "fare": fare["total_fare"] if fare else None,
            "base_fare": fare["base_fare"] if fare else None,
            "rate_per_km": fare["rate_per_km"] if fare else None,
            "stops": route_stop_summary(trip["route_id"]),
        })

    @app.route("/api/eta/<int:auto_id>")
    def eta_route(auto_id):
        trip = get_active_trip_for_auto(auto_id)
        if not trip:
            return jsonify({"success": False, "error": "No active trip."}), 404
        route_id = trip["route_id"]
        stops = route_stop_summary(route_id)
        if not stops:
            return jsonify({"success": False, "error": "No route stops found."}), 404
        current_lat = trip["current_lat"] if trip["current_lat"] is not None else stops[0]["latitude"]
        current_lng = trip["current_lng"] if trip["current_lng"] is not None else stops[0]["longitude"]
        current_progress = distance_along_route_km(current_lat, current_lng, stops)
        requested_drop_stop_id = request.args.get("drop_stop_id", type=int)
        destination = next((stop for stop in stops if stop["stop_id"] == requested_drop_stop_id), None)
        if requested_drop_stop_id and destination is None:
            return jsonify({"success": False, "error": "Drop-off stop is not on this route."}), 400
        destination = destination or stops[-1]
        destination_progress = float(destination.get("distance_from_start_km", current_progress))
        distance = abs(destination_progress - current_progress)
        eta = estimate_eta(distance, route_id, trip["current_speed_kmh"] or Config.DEFAULT_SPEED_KMH)
        return jsonify(
            {
                "success": True,
                "distance_km": round(distance, 2),
            "formula_eta_min": eta["formula_eta_min"],
            "ml_eta_min": eta["ml_eta_min"],
                "model_type": "Synthetic demo model",
            }
        )

    @app.route("/api/demand/<int:stop_id>")
    def demand_route(stop_id):
        estimated = predict_demand(stop_id)
        return jsonify({"success": True, "stop_id": stop_id, "estimated_passengers": round(float(estimated), 2)})

    @app.route("/api/autos/<int:auto_id>")
    def get_auto(auto_id):
        auto = db_fetch_one("SELECT * FROM autos WHERE auto_id = ?", (auto_id,))
        if not auto:
            return jsonify({"success": False, "error": "Auto not found."}), 404
        trip = get_active_trip_for_auto(auto_id)
        return jsonify({
            "success": True,
            "auto_id": auto_id,
            "vehicle_no": auto["vehicle_no"],
            "capacity": auto["capacity"],
            "route_id": auto["route_id"],
            "active_trip": trip is not None,
            "available_seats": available_seats_for_trip(trip["trip_id"]) if trip else auto["capacity"],
        })

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
