from db import db_fetch_all, db_fetch_one


def available_seats_for_trip(trip_id, pickup_stop_id=None, drop_stop_id=None):
    trip = db_fetch_one(
        "SELECT t.route_id, t.walkin_seats, a.capacity FROM trips t "
        "JOIN autos a ON a.auto_id = t.auto_id WHERE t.trip_id = ?",
        (trip_id,),
    )
    if not trip:
        return 0

    stops = db_fetch_all(
        "SELECT stop_id, stop_order FROM route_stops WHERE route_id = ? ORDER BY stop_order",
        (trip["route_id"],),
    )
    if len(stops) < 2:
        return max(int(trip["capacity"]) - int(trip["walkin_seats"] or 0), 0)

    stop_indexes = {stop["stop_id"]: index for index, stop in enumerate(stops)}
    if pickup_stop_id is not None and drop_stop_id is not None:
        pickup_index = stop_indexes.get(int(pickup_stop_id))
        drop_index = stop_indexes.get(int(drop_stop_id))
        if pickup_index is None or drop_index is None or pickup_index == drop_index:
            return 0
        first_segment, last_segment = sorted((pickup_index, drop_index))
        segments = range(first_segment, last_segment)
    else:
        segments = range(len(stops) - 1)

    bookings = db_fetch_all(
        "SELECT pickup_stop_id, drop_stop_id FROM bookings "
        "WHERE trip_id = ? AND status IN ('BOOKED', 'ONBOARD')",
        (trip_id,),
    )
    occupied_by_segment = []
    for segment_index in segments:
        segment_start = int(stops[segment_index]["stop_order"])
        segment_end = int(stops[segment_index + 1]["stop_order"])
        occupied = 0
        for booking in bookings:
            booking_pickup = stop_indexes.get(booking["pickup_stop_id"])
            booking_drop = stop_indexes.get(booking["drop_stop_id"])
            if booking_pickup is None or booking_drop is None:
                continue
            booked_start = int(stops[min(booking_pickup, booking_drop)]["stop_order"])
            booked_end = int(stops[max(booking_pickup, booking_drop)]["stop_order"])
            if booked_start <= segment_start and booked_end >= segment_end:
                occupied += 1
        occupied_by_segment.append(occupied)

    if not occupied_by_segment:
        return 0
    return max(
        int(trip["capacity"]) - int(trip["walkin_seats"] or 0) - max(occupied_by_segment),
        0,
    )