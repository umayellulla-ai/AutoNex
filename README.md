# AUTONEX

AUTONEX is a local shared-auto booking and tracking web application built with Flask, MySQL, Leaflet, OpenStreetMap, and scikit-learn.

## Project goal
The system helps passengers find nearby shared autos, track their live location, estimate fare and ETA, and book seats on active trips. Drivers can register a vehicle, a route, and start a trip in either real GPS mode or a demo GPS mode for local classroom demonstration.

## Architecture
- Frontend: HTML, CSS, JavaScript
- Backend: Python, Flask
- Database: MySQL (with SQLite fallback for local demo simplicity)
- Mapping: Leaflet + OpenStreetMap
- ML: scikit-learn on synthetic demo datasets

Domain logic is split into `services/geo.py`, `services/seats.py`, `services/fare.py`, `services/eta.py`, `services/demand.py`, and `services/simulator.py`. Parameterized database access lives in `db.py`. DEMO GPS positions are written by a server-side worker; REAL GPS positions are posted by the driver's browser. Passengers preview the route fare before confirming; the server recalculates fare and segment capacity at confirmation.

## Project structure
- app.py - Flask application and API routes
- db.py - MySQL connection helper with local SQLite fallback
- config.py - environment configuration
- database.sql - MySQL schema and demo seed data
- generate_datasets.py - deterministic SYNTHETIC training CSV generator
- train_model.py - ML training script
- dataset/eta_data.csv - synthetic ETA data
- dataset/demand_data.csv - synthetic demand data
- models/ - trained model outputs
- templates/base.html - shared document layout
- templates/ - HTML pages
- static/ - CSS and JavaScript files
- services/ - geospatial, seat, fare, ETA, demand, and simulator logic
- static/js/booking.js - fare preview and booking confirmation

## Quick start on Windows
1. Open PowerShell in the project folder.
2. Create a virtual environment:

```powershell
python --version
python -m venv venv
venv\Scripts\activate
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

4. Create the MySQL database and tables:

```powershell
mysql -u root -p < database.sql
```

5. Configure .env:

```env
DB_HOST=localhost
DB_PORT=3306
DB_NAME=autonex
DB_USER=root
DB_PASSWORD=your_mysql_password
SECRET_KEY=change-this-secret-key
DB_CONNECTION_TIMEOUT_SECONDS=2
GEOCODE_TIMEOUT_SECONDS=8
MAX_LOCATION_LENGTH=250
MIN_TRIP_DISTANCE_KM=0.05
DEFAULT_SPEED_KMH=18
DEMO_GPS_INTERVAL_SECONDS=3
```

6. Train ML models:

```powershell
python train_model.py
```

This creates the synthetic CSV files first when they are absent or empty, then trains and evaluates the ETA and demand models.

7. Start the app:

```powershell
python app.py
```

8. Open the browser:

```text
http://127.0.0.1:5000
```

## Demo accounts
- Admin: admin@autonex.com / admin123
- Driver: driver@autonex.com / driver123
- Passenger: passenger@autonex.com / pass123

## Demo procedure
1. Start MySQL, or leave `USE_SQLITE_FALLBACK=1` for local SQLite mode.
2. Open PowerShell in AUTONEX and activate `venv`.
3. Run `python app.py`.
4. Open `http://127.0.0.1:5000`.
5. Log in as `driver@autonex.com` with `driver123`.
6. Click **START DEMO TRIP** and confirm the driver status says **DEMO GPS**.
7. Open a separate passenger browser session and log in as `passenger@autonex.com` with `pass123`.
8. Confirm the map and nearby-auto list show `KA01AB1234`, `KA03CD5678`, and `KA05EF9012`.
9. Click an auto marker or list selection and confirm its driver, seats, ETA, and route appear in the map card.
10. Click **◎ My Location** and allow browser location access.
11. Enter a pickup and destination, then click **Show route on map**.
12. Confirm pickup/drop markers, road route, and Stop A–E markers appear.
13. Select the auto and verify its location marker moves while DEMO GPS is active.
14. Choose **Individual** and click **Preview Fare**.
15. Check distance, base fare, rate per km, estimated fare, and ETA; click **Confirm booking**.
16. In a second passenger session, choose the same auto and route with **Shared**; verify the shared fare and backend seat count.
17. Complete the ride from the passenger trip page and verify the seat count increases again.
18. Repeat with a driver account and **REAL GPS** to test browser geolocation separately from DEMO GPS.

## Notes
- REAL GPS is supported through browser geolocation.
- DEMO GPS mode is included for reliability during classroom demonstration.
- ML models use synthetic demo data and are clearly labeled as such.
- This is suitable for a B.Tech major project demonstration and local development.
