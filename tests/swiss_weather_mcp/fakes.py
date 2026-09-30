"""Fake MeteoSwiss data and responses shared by the tests."""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

POINT_TABLE_COLUMNS = (
    "point_id;point_type_id;station_abbr;postal_code;point_name;point_type_de;point_type_fr;"
    "point_type_it;point_type_en;point_height_masl;point_coordinates_lv95_east;"
    "point_coordinates_lv95_north;point_coordinates_wgs84_lat;point_coordinates_wgs84_lon"
)
# Zurich twice so the lowest postal code has to win, Davos with no postal code so the station is
# the only option, and Wallisellen because it is what a near match for "Wallis" would wrongly pick.
# Coordinates are the real LV95 ones of these places, in metres.
POINT_TABLE_ROWS = (
    ("800200", "2", "", "8002", "Zürich", "431.0", "2682417.0", "1246175.0"),
    ("800100", "2", "", "8001", "Zürich", "409.0", "2683348.0", "1247414.0"),
    ("26", "1", "DAV", "", "Davos", "1594.0", "2783519.0", "1187459.0"),
    ("840000", "2", "", "8400", "Wallisellen", "441.0", "2687275.0", "1252173.0"),
)

RUN_ID = "202609221300"
EARLIER_RUN_ID = "202609221200"


def build_point_table_csv() -> bytes:
    lines = [POINT_TABLE_COLUMNS]
    for point_id, point_type_id, station_abbr, postal_code, name, altitude_m, east_m, north_m in POINT_TABLE_ROWS:
        lines.append(
            f"{point_id};{point_type_id};{station_abbr};{postal_code};{name};Ort;Lieu;Luogo;Place;{altitude_m};{east_m};{north_m};47.0;8.0"
        )
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def build_parameter_csv(parameter: str, values: dict, point: str = "800100;2") -> bytes:
    """Build a parameter file the way MeteoSwiss publishes it, header and all locations included."""
    lines = [f"point_id;point_type_id;Date;{parameter}"]
    for stamp, value in values.items():
        lines.append(f"{point};{stamp};{value}")
        lines.append(f"999999;2;{stamp};-1")  # another location, which must be filtered out
    return ("\n".join(lines) + "\n").encode("latin-1")


class FakeResponse:
    """Stand in for a streamed requests response."""

    def __init__(self, body: bytes = b"", status_code: int = 200, payload: Optional[dict] = None):
        self.body = body
        self.status_code = status_code
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        # Deliberately split mid line, so the chunk stitching in _write_rows_starting_with is exercised
        for start in range(0, len(self.body), 7):
            yield self.body[start:start + 7]


def build_stac_item(run_id: str, parameters) -> dict:
    return {
        "assets": {
            f"vnut12.lssw.{run_id}.{parameter}.csv": {"href": f"https://example.test/{run_id}/{parameter}.csv"}
            for parameter in parameters
        }
    }


def build_swiss_time(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp).replace(tzinfo=ZoneInfo("Europe/Zurich"))


# The columns of ogd-smn_meta_stations.csv the server reads, with real SwissMetNet stations. UEB has
# no thermometer, so it must be passed over, KLO has published no rows yet, and DAV stands where
# the Davos location point is.
STATION_TABLE_COLUMNS = (
    "station_abbr;station_name;station_canton;station_height_masl;"
    "station_coordinates_lv95_east;station_coordinates_lv95_north"
)
STATION_TABLE_ROWS = (
    ("SMA", "Zürich / Fluntern", "ZH", "604.0", "2685223.0", "1248410.0"),
    ("UEB", "Uetliberg", "ZH", "854.0", "2679455.0", "1245034.0"),
    ("KLO", "Zürich / Kloten", "ZH", "426.0", "2682711.0", "1259339.0"),
    ("DAV", "Davos", "GR", "1594.0", "2783519.0", "1187459.0"),
)

# Some columns of the now files, with real values, except that DAV has no rain and no wind direction.
# SMA has the row of 3 hours before its last one and the six rows of its last hour, DAV only one row.
NOW_VALUES_COLUMNS = (
    "station_abbr;reference_timestamp;tre200s0;ure200s0;tde200s0;pp0qnhs0;dkl010z0;fu3010z0;"
    "fu3010z1;rre150z0;sre000z0;gre000z0"
)
NOW_VALUES_ROWS = {
    "sma": (
        "SMA;25.09.2026 11:00;18.40;45.20;6.40;1022.80;12.00;5.40;11.50;0;10;489",
        "SMA;25.09.2026 13:10;20.50;36.00;4.90;1021.90;18.00;4.30;9.40;0;10;560",
        "SMA;25.09.2026 13:20;20.60;35.80;4.80;1021.80;20.00;4.00;9.00;0;10;549",
        "SMA;25.09.2026 13:30;20.60;35.60;4.70;1021.80;25.00;3.60;7.90;0;4;402",
        "SMA;25.09.2026 13:40;20.70;35.30;4.70;1021.70;28.00;4.10;8.30;0;0;268",
        "SMA;25.09.2026 13:50;20.80;35.10;4.60;1021.70;31.00;3.90;8.60;0;10;509",
        "SMA;25.09.2026 14:00;21.00;34.30;4.70;1021.60;23.00;4.70;10.10;0.00;10.00;516.00",
    ),
    "ueb": ("UEB;25.09.2026 14:00;;;;;;;;;10.00;561.00",),
    "klo": (),
    "dav": ("DAV;25.09.2026 14:00;16.50;17.70;-8.10;1024.10;;16.60;28.80;;10.00;537.00",),
}
# Five minutes after the latest rows of the now files
MEASUREMENTS_NOW = datetime(2026, 9, 25, 14, 5, tzinfo=timezone.utc)


def build_station_table_csv() -> bytes:
    lines = [STATION_TABLE_COLUMNS]
    for row in STATION_TABLE_ROWS:
        lines.append(";".join(row))
    return ("\n".join(lines) + "\n").encode("latin-1")


def build_now_values_csv(abbr: str) -> bytes:
    """Build a station's now file the way MeteoSwiss publishes it, with CRLF line ends."""
    lines = [NOW_VALUES_COLUMNS, *NOW_VALUES_ROWS[abbr]]
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")
