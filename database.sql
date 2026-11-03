-- =====================================================================
--  AUTONEX  -  MySQL schema + demo seed data
--  Run once (see README):   mysql -u root -p < database.sql
--  WARNING: the next line DELETES any existing "autonex" database so the
--           script can be re-run from scratch. Remove it to keep old data.
-- =====================================================================
DROP DATABASE IF EXISTS autonex;
CREATE DATABASE autonex CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE autonex;

-- ---------------------------------------------------------------------
-- 1. USERS  (one login table for all three roles)
--    Passwords are stored ONLY as salted hashes (Werkzeug pbkdf2:sha256).
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id        INT AUTO_INCREMENT PRIMARY KEY,
    full_name      VARCHAR(100)  NOT NULL,
    email          VARCHAR(120)  NOT NULL UNIQUE,
    phone          VARCHAR(15)   NOT NULL,
    password_hash  VARCHAR(255)  NOT NULL,
    role           ENUM('PASSENGER','DRIVER','ADMIN') NOT NULL,
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 2. DRIVERS / PASSENGERS  (role-specific profile, 1-to-1 with users)
-- ---------------------------------------------------------------------
CREATE TABLE drivers (
    driver_id       INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL UNIQUE,
    license_number  VARCHAR(30) NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE passengers (
    passenger_id  INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL UNIQUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 3. ROUTES and their ordered STOPS
--    distance_from_start_km = cumulative distance along the route.
--    Distance between any two stops = difference of this column,
--    which is what makes individual fares and segment-based seats possible.
-- ---------------------------------------------------------------------
CREATE TABLE routes (
    route_id             INT AUTO_INCREMENT PRIMARY KEY,
    route_name           VARCHAR(100) NOT NULL,
    source_name          VARCHAR(100) NOT NULL,
    destination_name     VARCHAR(100) NOT NULL,
    created_by_user_id   INT NULL,                 -- NULL = built-in route
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE route_stops (
    stop_id                 INT AUTO_INCREMENT PRIMARY KEY,
    route_id                INT NOT NULL,
    stop_order              INT NOT NULL,          -- 1 = first stop
    stop_name               VARCHAR(100) NOT NULL,
    latitude                DECIMAL(9,6) NOT NULL,
    longitude               DECIMAL(9,6) NOT NULL,
    distance_from_start_km  DECIMAL(8,3) NOT NULL DEFAULT 0,
    UNIQUE KEY uq_route_order (route_id, stop_order),
    FOREIGN KEY (route_id) REFERENCES routes(route_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 4. AUTOS  (one auto per driver, runs on one route)
-- ---------------------------------------------------------------------
CREATE TABLE autos (
    auto_id       INT AUTO_INCREMENT PRIMARY KEY,
    driver_id     INT NOT NULL UNIQUE,
    vehicle_no    VARCHAR(20) NOT NULL UNIQUE,
    capacity      TINYINT NOT NULL,
    route_id      INT NOT NULL,
    CONSTRAINT chk_capacity CHECK (capacity BETWEEN 1 AND 8),
    FOREIGN KEY (driver_id) REFERENCES drivers(driver_id) ON DELETE CASCADE,
    FOREIGN KEY (route_id)  REFERENCES routes(route_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 5. TRIPS  (one row each time a driver presses START TRIP / START DEMO TRIP)
--    current_lat/lng/progress_km hold the LATEST position so the passenger
--    map can read it with one fast query. Full history goes to `locations`.
--    walkin_seats = people riding who did not book through the app.
--    active_key: generated column + UNIQUE index = the DATABASE itself
--    guarantees an auto can never have two ACTIVE trips at once.
-- ---------------------------------------------------------------------
CREATE TABLE trips (
    trip_id            INT AUTO_INCREMENT PRIMARY KEY,
    auto_id            INT NOT NULL,
    route_id           INT NOT NULL,
    status             ENUM('ACTIVE','ENDED') NOT NULL DEFAULT 'ACTIVE',
    gps_mode           ENUM('REAL','DEMO')    NOT NULL,
    walkin_seats       TINYINT NOT NULL DEFAULT 0,
    current_lat        DECIMAL(9,6) NULL,
    current_lng        DECIMAL(9,6) NULL,
    current_speed_kmh  DECIMAL(5,1) NULL,
    progress_km        DECIMAL(8,3) NOT NULL DEFAULT 0,   -- distance travelled along the route
    last_update        DATETIME NULL,
    started_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at           DATETIME NULL,
    active_key         INT GENERATED ALWAYS AS (IF(status = 'ACTIVE', auto_id, NULL)) STORED,
    UNIQUE KEY uq_one_active_trip (active_key),
    KEY idx_trip_status (status),
    FOREIGN KEY (auto_id)  REFERENCES autos(auto_id),
    FOREIGN KEY (route_id) REFERENCES routes(route_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 6. LOCATIONS  (GPS history; `source` records REAL vs DEMO honestly)
-- ---------------------------------------------------------------------
CREATE TABLE locations (
    location_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
    trip_id      INT NOT NULL,
    latitude     DECIMAL(9,6) NOT NULL,
    longitude    DECIMAL(9,6) NOT NULL,
    speed_kmh    DECIMAL(5,1) NULL,
    source       ENUM('REAL','DEMO') NOT NULL,
    recorded_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_loc_trip_time (trip_id, recorded_at),
    FOREIGN KEY (trip_id) REFERENCES trips(trip_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 7. BOOKINGS  (each passenger's own journey on a shared trip)
--    BOOKED   = seat reserved, waiting at pickup stop
--    ONBOARD  = passenger is inside the auto
--    COMPLETED / CANCELLED = finished
--    active_passenger_key: UNIQUE -> a passenger cannot hold two active
--    bookings at once ("passenger already inside an auto").
-- ---------------------------------------------------------------------
CREATE TABLE bookings (
    booking_id           INT AUTO_INCREMENT PRIMARY KEY,
    trip_id              INT NOT NULL,
    passenger_id         INT NOT NULL,
    pickup_stop_id       INT NOT NULL,
    drop_stop_id         INT NOT NULL,
    booking_type         ENUM('INDIVIDUAL','SHARED') NOT NULL DEFAULT 'INDIVIDUAL',
    distance_km          DECIMAL(8,3) NOT NULL,
    status               ENUM('BOOKED','ONBOARD','COMPLETED','CANCELLED') NOT NULL DEFAULT 'BOOKED',
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    boarded_at           DATETIME NULL,
    completed_at         DATETIME NULL,
    active_passenger_key INT GENERATED ALWAYS AS
                         (IF(status IN ('BOOKED','ONBOARD'), passenger_id, NULL)) STORED,
    UNIQUE KEY uq_one_active_booking (active_passenger_key),
    KEY idx_booking_trip_status (trip_id, status),
    FOREIGN KEY (trip_id)        REFERENCES trips(trip_id),
    FOREIGN KEY (passenger_id)   REFERENCES passengers(passenger_id),
    FOREIGN KEY (pickup_stop_id) REFERENCES route_stops(stop_id),
    FOREIGN KEY (drop_stop_id)   REFERENCES route_stops(stop_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 8. FARE SETTINGS (configurable) and FARE RECORDS (one per booking)
--    fare = base_fare + distance_km * rate_per_km   (never below min_fare)
--    fare_records stores a SNAPSHOT of the rates used, so changing the
--    rates later does not alter fares already quoted.
-- ---------------------------------------------------------------------
CREATE TABLE fare_settings (
    setting_id    INT AUTO_INCREMENT PRIMARY KEY,
    base_fare     DECIMAL(8,2) NOT NULL,
    rate_per_km   DECIMAL(8,2) NOT NULL,
    min_fare      DECIMAL(8,2) NOT NULL DEFAULT 0,
    currency      VARCHAR(5)   NOT NULL DEFAULT 'INR',
    is_active     TINYINT(1)   NOT NULL DEFAULT 1,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE fare_records (
    fare_id        INT AUTO_INCREMENT PRIMARY KEY,
    booking_id     INT NOT NULL UNIQUE,
    distance_km    DECIMAL(8,3) NOT NULL,
    base_fare      DECIMAL(8,2) NOT NULL,
    rate_per_km    DECIMAL(8,2) NOT NULL,
    total_fare     DECIMAL(8,2) NOT NULL,
    calculated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 9. DEMAND DATA  (boardings per stop per hour)
--    data_source = 'SYNTHETIC' for generated demo data,
--                  'APP'       for boardings actually recorded by AUTONEX.
-- ---------------------------------------------------------------------
CREATE TABLE demand_data (
    demand_id        INT AUTO_INCREMENT PRIMARY KEY,
    stop_id          INT NOT NULL,
    observed_at      DATETIME NOT NULL,
    hour_of_day      TINYINT NOT NULL,
    day_of_week      TINYINT NOT NULL,            -- 0 = Monday ... 6 = Sunday
    passenger_count  INT NOT NULL,
    data_source      ENUM('SYNTHETIC','APP') NOT NULL DEFAULT 'APP',
    KEY idx_demand_stop (stop_id, hour_of_day),
    FOREIGN KEY (stop_id) REFERENCES route_stops(stop_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 10. ETA PREDICTIONS  (log: formula ETA vs ML ETA, for evaluation)
-- ---------------------------------------------------------------------
CREATE TABLE eta_predictions (
    prediction_id   INT AUTO_INCREMENT PRIMARY KEY,
    trip_id         INT NOT NULL,
    booking_id      INT NULL,
    stop_id         INT NOT NULL,                 -- the stop the ETA is for
    distance_km     DECIMAL(8,3) NOT NULL,
    formula_eta_min DECIMAL(7,2) NOT NULL,        -- distance / assumed speed
    ml_eta_min      DECIMAL(7,2) NULL,            -- NULL when model file is missing
    model_used      ENUM('FORMULA','ML') NOT NULL,
    predicted_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_eta_trip (trip_id, predicted_at),
    FOREIGN KEY (trip_id)    REFERENCES trips(trip_id) ON DELETE CASCADE,
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id) ON DELETE SET NULL,
    FOREIGN KEY (stop_id)    REFERENCES route_stops(stop_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- 11. NOTIFICATIONS (real account events shown in the passenger Inbox)
-- ---------------------------------------------------------------------
CREATE TABLE notifications (
    notification_id  INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT NOT NULL,
    title            VARCHAR(160) NOT NULL,
    body             TEXT NOT NULL,
    notification_type VARCHAR(20) NOT NULL DEFAULT 'UPDATES',
    is_read          TINYINT(1) NOT NULL DEFAULT 0,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_notifications_user (user_id, created_at),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;


-- =====================================================================
--  SEED / DEMO DATA
-- =====================================================================

-- Fare rates (DEMO values - edit freely, or change from the admin page later)
INSERT INTO fare_settings (base_fare, rate_per_km, min_fare, currency)
VALUES (10.00, 8.00, 15.00, 'INR');

-- Demo accounts. Passwords (hashed below):
--   admin@autonex.com       Admin@123
--   driver1@autonex.com     Driver@123      driver2@autonex.com  Driver@123
--   passenger1@autonex.com  Passenger@123   passenger2@autonex.com / passenger3@autonex.com same
INSERT INTO users (full_name, email, phone, password_hash, role) VALUES
('AUTONEX Admin',   'admin@autonex.com',      '9000000001', 'pbkdf2:sha256:1000000$8NrEIHSAdUa2A2Fj$986fc8c32f3f5b55a466d4cc6b9cfae1e40441ef65e51d9c69532da3de415431', 'ADMIN'),
('Ravi Kumar',      'driver1@autonex.com',    '9000000002', 'pbkdf2:sha256:1000000$7d58Znn8HruadS7D$6da01337e06ca4e8d89003bdccea66c1a68777374fa20b879f08b14a0bafe023', 'DRIVER'),
('Suresh Reddy',    'driver2@autonex.com',    '9000000003', 'pbkdf2:sha256:1000000$7d58Znn8HruadS7D$6da01337e06ca4e8d89003bdccea66c1a68777374fa20b879f08b14a0bafe023', 'DRIVER'),
('Anita Sharma',    'passenger1@autonex.com', '9000000004', 'pbkdf2:sha256:1000000$vWRlZx29n86ZdW4d$5d3af1db3f3b29e81fefab38f37ef8c097dc4a43448a081ad50c6296be3aa814', 'PASSENGER'),
('Kiran Rao',       'passenger2@autonex.com', '9000000005', 'pbkdf2:sha256:1000000$vWRlZx29n86ZdW4d$5d3af1db3f3b29e81fefab38f37ef8c097dc4a43448a081ad50c6296be3aa814', 'PASSENGER'),
('Meena Joseph',    'passenger3@autonex.com', '9000000006', 'pbkdf2:sha256:1000000$vWRlZx29n86ZdW4d$5d3af1db3f3b29e81fefab38f37ef8c097dc4a43448a081ad50c6296be3aa814', 'PASSENGER');

INSERT INTO drivers (user_id, license_number) VALUES (2, 'DL-DEMO-0001'), (3, 'DL-DEMO-0002');
INSERT INTO passengers (user_id) VALUES (4), (5), (6);

-- Demo route A -> B -> C -> D -> E  (about 3.6 km, short so a demo trip takes only a few minutes).
-- NOTE: these coordinates are APPROXIMATE DEMO VALUES, not surveyed road points.
--       Open the map; if the line does not follow a road you like, just edit
--       the latitude/longitude below (keep stop_order) and re-run this file.
--       distance_from_start_km = straight-line (haversine) distance accumulated stop to stop.
INSERT INTO routes (route_id, route_name, source_name, destination_name)
VALUES (1, 'Demo Route A to E', 'Stop A', 'Stop E');

INSERT INTO route_stops (route_id, stop_order, stop_name, latitude, longitude, distance_from_start_km) VALUES
(1, 1, 'Stop A', 17.401000, 78.559000, 0.000),
(1, 2, 'Stop B', 17.405000, 78.564500, 0.734),
(1, 3, 'Stop C', 17.408500, 78.572000, 1.620),
(1, 4, 'Stop D', 17.414000, 78.577000, 2.429),
(1, 5, 'Stop E', 17.420500, 78.586000, 3.627);

-- Two demo autos on the same route (so the passenger map shows more than one).
INSERT INTO autos (driver_id, vehicle_no, capacity, route_id) VALUES
(1, 'TS09UA1234', 4, 1),
(2, 'TS09UB5678', 3, 1);

-- Quick check (run manually if you like):
--   SELECT * FROM route_stops;
--   SELECT a.vehicle_no, a.capacity, r.route_name FROM autos a JOIN routes r USING (route_id);
