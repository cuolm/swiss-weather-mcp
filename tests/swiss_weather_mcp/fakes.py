"""Fake MeteoSwiss, SLF and swisstopo data and responses shared by the tests."""
import io
import json
import sqlite3
import struct
import zipfile
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

POINT_TABLE_COLUMNS = (
    "point_id;point_type_id;station_abbr;postal_code;point_name;point_type_de;point_type_fr;"
    "point_type_it;point_type_en;point_height_masl;point_coordinates_lv95_east;"
    "point_coordinates_lv95_north;point_coordinates_wgs84_lat;point_coordinates_wgs84_lon"
)
# Zürich 8001 and 8002 to choose between by distance and height, Davos, a station with no postal
# code and no forecast values, and Wallisellen, more than 5 km from Zürich. Flachland Deutschschweiz
# is a regional forecast 8 m from the Buchs / Aarau station. Coordinates are the real LV95 ones of
# these points, in metres.
POINT_TABLE_ROWS = (
    ("800200", "2", "", "8002", "Zürich", "431.0", "2682417.0", "1246175.0"),
    ("800100", "2", "", "8001", "Zürich", "409.0", "2683348.0", "1247414.0"),
    ("26", "1", "DAV", "", "Davos", "1594.0", "2783519.0", "1187459.0"),
    ("840000", "2", "", "8400", "Wallisellen", "441.0", "2687275.0", "1252173.0"),
    ("230", "3", "", "", "Flachland Deutschschweiz", "387.0", "2648390.0", "1248365.0"),
    ("67", "1", "BUS", "", "Buchs / Aarau", "387.0", "2648396.0", "1248370.0"),
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
    """Build a parameter file the way MeteoSwiss publishes it, header and all points included."""
    lines = [f"point_id;point_type_id;Date;{parameter}"]
    for stamp, value in values.items():
        lines.append(f"{point};{stamp};{value}")
        lines.append(f"999999;2;{stamp};-1")  # another point, which must be filtered out
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
# the Davos MeteoSwiss point is.
STATION_TABLE_COLUMNS = (
    "station_abbr;station_name;station_canton;station_data_since;station_height_masl;"
    "station_coordinates_lv95_east;station_coordinates_lv95_north"
)
STATION_TABLE_ROWS = (
    ("SMA", "Zürich / Fluntern", "ZH", "01.01.1864", "604.0", "2685223.0", "1248410.0"),
    ("UEB", "Uetliberg", "ZH", "14.11.1991", "854.0", "2679455.0", "1245034.0"),
    ("KLO", "Zürich / Kloten", "ZH", "01.01.1935", "426.0", "2682711.0", "1259339.0"),
    ("DAV", "Davos", "GR", "01.01.1864", "1594.0", "2783519.0", "1187459.0"),
)

# Some columns of the now files, with real values, except that DAV has no rain and no wind direction,
# and a made-up snow depth of 12 cm. SMA has the row of 3 hours before its last one and the six rows of
# its last hour, DAV only one row.
STATION_NOW_VALUES_COLUMNS = (
    "station_abbr;reference_timestamp;tre200s0;ure200s0;tde200s0;pp0qnhs0;dkl010z0;fu3010z0;"
    "fu3010z1;rre150z0;sre000z0;gre000z0;htoauts0"
)
STATION_NOW_VALUES_ROWS = {
    "sma": (
        "SMA;25.09.2026 11:00;18.40;45.20;6.40;1022.80;12.00;5.40;11.50;0;10;489;0",
        "SMA;25.09.2026 13:10;20.50;36.00;4.90;1021.90;18.00;4.30;9.40;0;10;560;0",
        "SMA;25.09.2026 13:20;20.60;35.80;4.80;1021.80;20.00;4.00;9.00;0;10;549;0",
        "SMA;25.09.2026 13:30;20.60;35.60;4.70;1021.80;25.00;3.60;7.90;0;4;402;0",
        "SMA;25.09.2026 13:40;20.70;35.30;4.70;1021.70;28.00;4.10;8.30;0;0;268;0",
        "SMA;25.09.2026 13:50;20.80;35.10;4.60;1021.70;31.00;3.90;8.60;0;10;509;0",
        "SMA;25.09.2026 14:00;21.00;34.30;4.70;1021.60;23.00;4.70;10.10;0.00;10.00;516.00;0",
    ),
    "ueb": ("UEB;25.09.2026 14:00;;;;;;;;;10.00;561.00;",),
    "klo": (),
    "dav": ("DAV;25.09.2026 14:00;16.50;17.70;-8.10;1024.10;;16.60;28.80;;10.00;537.00;12",),
}
# Rows of ogd-smn_meta_datainventory.csv, real except that KLO's snow depth is given an end day, as
# for a station that no longer measures it. UEB measures no snow depth.
DATA_INVENTORY_CSV = (
    "station_abbr;parameter_shortname;meas_cat_nr;data_since;data_till;owner\n"
    "DAV;htoauts0;1;01.02.2009 00:00;;MeteoSchweiz\n"
    "DAV;tre200s0;1;01.02.2004 00:00;;MeteoSchweiz\n"
    "KLO;htoauts0;1;11.05.2021 12:00;30.09.2025 23:50;MeteoSchweiz\n"
    "KLO;tre200s0;1;01.02.2004 00:00;;MeteoSchweiz\n"
    "SMA;htoauts0;1;14.05.2024 14:00;;MeteoSchweiz\n"
    "SMA;tre200s0;1;01.02.2004 00:00;;MeteoSchweiz\n"
    "UEB;tre200s0;1;14.11.1991 00:00;;MeteoSchweiz\n"
)
# Rows of VQHA80.csv at the time of the latest rows of the now files. UEB measures neither the
# temperature nor the rain, KLO had 4 minutes of sun and some rain, and DAV measures no rain. MRP is
# published there but missing from the station table.
ALL_STATIONS_LATEST_VALUES_CSV = (
    "Station/Location;Date;tre200s0;rre150z0;sre000z0;gre000z0;ure200s0;tde200s0;dkl010z0;fu3010z0;"
    "fu3010z1;prestas0;pp0qffs0;pp0qnhs0;ppz850s0;ppz700s0;dv1towz0;fu3towz0;fu3towz1;ta1tows0;uretows0;tdetows0\n"
    "SMA;202609251400;21.00;0.00;10.00;516.00;34.30;4.70;23.00;4.70;10.10;950.60;1018.90;1021.60;-;-;-;-;-;18.40;39.70;4.50\n"
    "UEB;202609251400;-;-;10.00;561.00;-;-;-;-;-;-;-;-;-;-;16.00;7.60;13.00;15.40;46.20;3.90\n"
    "KLO;202609251400;22.40;0.30;4.00;301.00;40.10;8.10;250.00;6.10;14.80;966.20;1019.00;1021.40;-;-;-;-;-;-;-;-\n"
    "DAV;202609251400;16.50;-;10.00;537.00;17.70;-8.10;-;16.60;28.80;845.00;-;1024.10;1545.30;-;-;-;-;-;-;-\n"
    "MRP;202609251400;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-;-\n"
)
# Five minutes after the latest rows of the now files
MEASUREMENTS_NOW = datetime(2026, 9, 25, 14, 5, tzinfo=timezone.utc)

# Some columns of the daily files, with real rows. SMA has the days up to yesterday, 2026-09-24, and
# a few earlier ones; on 1975-07-01 it has only the rain from 6 to 6 UTC, rre150d0, as the rain from 0 to 0
# UTC starts in 1981. UEB has no thermometer, and the other stations have no rows.
STATION_DAILY_VALUES_COLUMNS = (
    "station_abbr;reference_timestamp;tre200d0;tre200dx;tre200dn;rka150d0;rre150d0;sre000d0;fu3010d1"
)
STATION_AND_PERIOD_TO_DAYS_ROWS = {
    ("sma", "historical"): (
        "SMA;01.07.1975 00:00;9.8;13;8.4;;9.5;90;",
        "SMA;30.12.2025 00:00;-1.6;1.3;-3.8;0;0;49;25.6",
        "SMA;31.12.2025 00:00;-3.6;-0.3;-6.2;0;0;456;22.3",
    ),
    ("sma", "recent"): (
        "SMA;22.09.2026 00:00;13.7;19.9;8.3;0;0;685;31",
        "SMA;23.09.2026 00:00;14.3;21.4;6.9;0;0;663;20.5",
        "SMA;24.09.2026 00:00;15.3;21.8;10.9;0;0;183;30.2",
    ),
    ("ueb", "recent"): (
        "UEB;23.09.2026 00:00;;;;;;678;",
        "UEB;24.09.2026 00:00;;;;;;89;",
    ),
}


def build_station_table_csv() -> bytes:
    lines = [STATION_TABLE_COLUMNS]
    for row in STATION_TABLE_ROWS:
        lines.append(";".join(row))
    return ("\n".join(lines) + "\n").encode("latin-1")


def build_station_now_values_csv(abbr: str) -> bytes:
    """Build a station's now file the way MeteoSwiss publishes it, with CRLF line ends."""
    lines = [STATION_NOW_VALUES_COLUMNS, *STATION_NOW_VALUES_ROWS[abbr]]
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def build_station_daily_values_csv(abbr: str, period: str) -> bytes:
    """Build a station's daily file of a period, "recent" or "historical", the way MeteoSwiss publishes it."""
    rows = STATION_AND_PERIOD_TO_DAYS_ROWS.get((abbr, period), ())
    lines = [STATION_DAILY_VALUES_COLUMNS, *rows]
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


# Four objects of the SLF station list, as SLF publishes them: the IMIS stations that suit the
# Matterhorn best, in that order.
SLF_STATIONS = (
    {"code": "ZER1", "label": "Platthorn", "lon": 7.7363289398, "lat": 46.0536592487, "elevation": 3345.0,
     "country_code": "CH", "canton_code": "VS", "type": "WIND"},
    {"code": "GOR2", "label": "Gornergratsee", "lon": 7.7835986582, "lat": 45.987191604, "elevation": 2953.0,
     "country_code": "CH", "canton_code": "VS", "type": "SNOW_FLAT"},
    {"code": "ARO1", "label": "La Cassorte", "lon": 7.4352901353, "lat": 46.0353666322, "elevation": 3300.0,
     "country_code": "CH", "canton_code": "VS", "type": "WIND"},
    {"code": "ZER2", "label": "Triftchumme", "lon": 7.7274051006, "lat": 46.0421771157, "elevation": 2752.0,
     "country_code": "CH", "canton_code": "VS", "type": "SNOW_FLAT"},
)


# The temperature, humidity, wind, gust, direction, snow depth and snow surface temperature of a row of an IMIS station
SLF_ROW_KEYS = (
    "TA_30MIN_MEAN", "RH_30MIN_MEAN", "VW_30MIN_MEAN", "VW_30MIN_MAX", "DW_30MIN_MEAN", "HS", "TSS_30MIN_MEAN",
)


def build_slf_row(code: str, utc_time: str, values: tuple) -> dict:
    """Build one row of a station's measurements with the values of SLF_ROW_KEYS."""
    row = {"station_code": code, "measure_date": f"2026-09-25T{utc_time}:00Z"}
    for key, value in zip(SLF_ROW_KEYS, values):
        row[key] = value
    return row


# The last two rows of each station's measurements, with real values of 2026-11-09 moved to the time of
# the MeteoSwiss now files. Platthorn's temperature is left out, and Gornergratsee's last row is from
# 12:30 UTC, more than 1 hour before MEASUREMENTS_NOW. The WIND stations Platthorn and La Cassorte
# measure no snow depth and no snow surface temperature.
SLF_STATION_CODE_TO_ROWS = {
    "ZER1": (
        build_slf_row("ZER1", "13:30", (None, 77.1, 3.394, 8.12, 223.7, None, None)),
        build_slf_row("ZER1", "14:00", (None, 71.3, 4.584, 11.28, 235.8, None, None)),
    ),
    "GOR2": (
        build_slf_row("GOR2", "12:00", (0.663, 63.36, 3.352, 8.02, 236.5, 21.422, -2.078)),
        build_slf_row("GOR2", "12:30", (0.216, 63.08, 4.325, 7.922, 227.6, 21.412, -4.002)),
    ),
    "ARO1": (
        build_slf_row("ARO1", "13:30", (-1.009, 76.1, 3.033, 6.277, 199.2, None, None)),
        build_slf_row("ARO1", "14:00", (-0.713, 73.6, 2.318, 4.557, 198.8, None, None)),
    ),
    "ZER2": (
        build_slf_row("ZER2", "13:30", (1.762, 65.83, 0.16, 1.897, 75.6, 7.75, -1.848)),
        build_slf_row("ZER2", "14:00", (2.345, 61.94, 0.507, 2.836, 22.92, 6.98, -2.339)),
    ),
}

# The IMIS daily snow of the 4 days up to MEASUREMENTS_NOW, with real values of 2026-11-07 to 2026-11-10
# moved to those days, the newest day first as SLF publishes them. Triftchumme's new snow of 2026-09-24
# is left out.
SLF_DAILY_SNOW = (
    {"station_code": "GOR2", "measure_date": "2026-09-25T00:00:00", "HS": 21.0, "HN_1D": 0.0},
    {"station_code": "ZER2", "measure_date": "2026-09-25T00:00:00", "HS": 8.0, "HN_1D": 0.0},
    {"station_code": "GOR2", "measure_date": "2026-09-24T00:00:00", "HS": 22.0, "HN_1D": 0.0},
    {"station_code": "ZER2", "measure_date": "2026-09-24T00:00:00", "HS": 9.0, "HN_1D": None},
    {"station_code": "GOR2", "measure_date": "2026-09-23T00:00:00", "HS": 25.0, "HN_1D": 0.0},
    {"station_code": "ZER2", "measure_date": "2026-09-23T00:00:00", "HS": 11.0, "HN_1D": 3.8},
    {"station_code": "GOR2", "measure_date": "2026-09-22T00:00:00", "HS": 27.0, "HN_1D": 4.2},
    {"station_code": "ZER2", "measure_date": "2026-09-22T00:00:00", "HS": 13.0, "HN_1D": 0.0},
)

# Two objects of the SLF manual site list, as SLF publishes them
SLF_MANUAL_SITES = (
    {"code": "5DF0", "label": "Davos Flüelastr.", "lon": 9.8482749954, "lat": 46.8127039463, "elevation": 1560.0,
     "country_code": "CH", "canton_code": "GR"},
    {"code": "4ZE0", "label": "Zermatt", "lon": 7.7506291923, "lat": 46.0237554415, "elevation": 1600.0,
     "country_code": "CH", "canton_code": "VS"},
)

# The days of each manual site, with made-up snow. Zermatt's last day is from 2026-09-20, too old to count.
SLF_MANUAL_SITE_CODE_TO_ROWS = {
    "5DF0": (
        {"station_code": "5DF0", "measure_date": "2026-09-23T06:00:00Z", "HS": 18.0, "HN_1D": 2.0, "HNW_1D": 2.4},
        {"station_code": "5DF0", "measure_date": "2026-09-24T06:00:00Z", "HS": 25.0, "HN_1D": 8.0, "HNW_1D": 9.1},
        {"station_code": "5DF0", "measure_date": "2026-09-25T06:00:00Z", "HS": 31.0, "HN_1D": 6.0, "HNW_1D": 6.8},
    ),
    "4ZE0": (
        {"station_code": "4ZE0", "measure_date": "2026-09-20T06:00:00Z", "HS": 10.0, "HN_1D": 0.0, "HNW_1D": 0.0},
    ),
}


def build_slf_stations_json() -> bytes:
    return json.dumps(SLF_STATIONS).encode("utf-8")


def build_slf_measurements_json(code: str) -> bytes:
    rows = SLF_STATION_CODE_TO_ROWS[code]
    return json.dumps(rows).encode("utf-8")


def build_slf_daily_snow_json() -> bytes:
    return json.dumps(SLF_DAILY_SNOW).encode("utf-8")


def build_slf_manual_sites_json() -> bytes:
    return json.dumps(SLF_MANUAL_SITES).encode("utf-8")


def build_slf_manual_site_days_json(code: str) -> bytes:
    rows = SLF_MANUAL_SITE_CODE_TO_ROWS[code]
    return json.dumps(rows).encode("utf-8")


# Two real bulletins of the SLF bulletin API V4, with fewer regions, shorter texts and square outlines.
# The first is the one of Braunwald on 2026-02-14 at 08:00; its outline has a hole around Flims Dorf.
# The second is the one of Davos on 2026-04-25, with a later danger level for the wet snow, a wet-snow
# problem without heights, aspects or summary, and no weather forecast or review, as SLF leaves them out
# on some days. Its outline has a part around Davos and one around the Matterhorn.
AVALANCHE_BULLETINS = (
    {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[8.8, 46.7], [9.5, 46.7], [9.5, 47.1], [8.8, 47.1], [8.8, 46.7]],
                [[9.2, 46.78], [9.4, 46.78], [9.4, 46.88], [9.2, 46.88], [9.2, 46.78]],
            ],
        },
        "properties": {
            "validTime": {"startTime": "2026-02-14T07:00:00Z", "endTime": "2026-02-14T16:00:00Z"},
            "nextUpdate": "2026-02-14T16:00:00Z",
            "unscheduled": False,
            "regions": [
                {"regionID": "CH-3112", "name": "Southern Glarus-Grosstal"},
                {"regionID": "CH-2122", "name": "Engelberg"},
                {"regionID": "CH-5121", "name": "Calanda"},
            ],
            "dangerRatings": [
                {"mainValue": "considerable", "validTimePeriod": "all_day", "customData": {"CH": {"subdivision": "neutral"}}},
            ],
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "comment": "Avalanches can be released, even by a single winter sport participant. <br/> "
                               "Backcountry touring calls for experience in the assessment of avalanche danger and caution.",
                    "dangerRatingValue": "considerable",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "1800"},
                    "aspects": ["N", "NE", "E", "SE", "W", "NW"],
                    "customData": {"CH": {
                        "subdivision": "neutral",
                        "coreZoneText": "Danger level \"considerable\" (3=) in west to northeast to southeast facing "
                                        "aspects above 1800m.",
                    }},
                },
            ],
            "publicationTime": "2026-02-14T07:00:00Z",
            "weatherForecast": {"comment": "<h1>Weather forecast to Saturday</h1><p>On Saturday, it will be very cloudy."
                                           "</p><h2 contenteditable=\"false\">Fresh snow</h2><ul><li>Simplon area, "
                                           "western Ticino: 5 to 10 cm</li></ul>"},
            "weatherReview": {"comment": "<h1>Weather review for Friday</h1><p>A little snow fell in the north above "
                                         "1000 m during the night.</p>"},
            "snowpackStructure": {"comment": "<h1>Snowpack</h1><ul><li>Prealps: newer snowdrift accumulations lie on "
                                             "a mostly favourable snowpack.</li></ul>"},
            "tendency": [{"comment": "<h1>Outlook</h1><h2>Sunday</h2><p>The avalanche danger will continue to "
                                     "decrease in the west.</p>"}],
            "fill": "#FF9900",
        },
    },
    {
        "type": "Feature",
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[9.6, 46.6], [10.2, 46.6], [10.2, 47.0], [9.6, 47.0], [9.6, 46.6]]],
                [[[7.5, 45.9], [7.9, 45.9], [7.9, 46.1], [7.5, 46.1], [7.5, 45.9]]],
            ],
        },
        "properties": {
            "validTime": {"startTime": "2026-04-24T15:00:00Z", "endTime": "2026-04-25T15:00:00Z"},
            "nextUpdate": "2026-04-25T15:00:00Z",
            "unscheduled": False,
            "regions": [
                {"regionID": "CH-5111", "name": "Northern Prättigau"},
                {"regionID": "CH-5123", "name": "Davos"},
            ],
            "dangerRatings": [
                {"mainValue": "low", "validTimePeriod": "all_day"},
                {"mainValue": "moderate", "validTimePeriod": "later"},
            ],
            "avalancheProblems": [
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "Faceted weak layers exist in the snowpack in particular on steep shady slopes.",
                    "dangerRatingValue": "low",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                    "aspects": ["N", "NE", "E", "SE", "W", "NW"],
                    "customData": {"CH": {
                        "coreZoneText": "Danger level \"low\" (1) in west to northeast to southeast facing aspects "
                                        "above 2200m.",
                    }},
                },
                {
                    "problemType": "wet_snow",
                    "comment": "As the day progresses there will be an increase in the danger of wet avalanches.",
                    "dangerRatingValue": "moderate",
                    "validTimePeriod": "later",
                },
            ],
            "publicationTime": "2026-04-24T15:00:00Z",
            "snowpackStructure": {"comment": "<h1>Snowpack</h1><p>Moisture penetration into the snowpack is well "
                                             "advanced.</p>"},
            "tendency": [{"comment": "<h1>Outlook to Monday</h1><p>After a clear night, touring conditions will be "
                                     "favourable in the early morning.</p>"}],
            "fill": "#FFFF00",
        },
    },
)


def build_avalanche_bulletins_geojson() -> bytes:
    feature_collection = {"type": "FeatureCollection", "features": list(AVALANCHE_BULLETINS)}
    return json.dumps(feature_collection).encode("utf-8")


# Some columns of the swissNAMES3D files, with real places. Oerlikon is a city district in the
# points file and again in the areas file, where it is not read. Rüti is a field name, which is
# not read either, and Zürich HB a railway station. The Rheinfall is a waterfall. Blausee is the
# name of three places: a place and a district 280 m apart, which are one place, and a lake near
# Wallisellen. Uetliberg is a hill and the hamlet on it. Lindenhof lies next to Zürich 8001. Both
# Moos are nearest to Wallisellen, and one Seeli lies 12 km from Wallisellen. Zurich and Genf are
# the French and German names of Zürich and Genève, in the same spot. Wil SG is the town, and two
# smaller districts are called Wil. Biel/Bienne has a name in two languages, like its railway
# station, and the shooting range of Küsnacht a name with slashes. No row is called Davos: Davos
# Dorf and Davos Platz are. No row is called Flims either, and Flums is a bigger town spelled alike.
# Grindelwald is a village and a hamlet in canton Luzern, so the village is the obvious one.
# The rows from Zürich on are exactly as swisstopo publishes them.
PLACE_NAMES_COLUMNS = "UUID;NAME;STATUS;NAMEN_TYP;OBJEKTART;EINWOHNERKATEGORIE;E;N;Z"
PLACE_NAMES_FILE_TO_ROWS = {
    "swissNAMES3D_PKT.csv": (
        "{1};Oerlikon;offiziell;einfacher Name;Quartier;10'000 bis 49'999;2683618;1251448;444",
        "{2};Rüti;offiziell;einfacher Name;Flurname swisstopo;k_W;2707453;1226029;526",
        "{3};Zürich HB;offiziell;einfacher Name;Haltestelle Bahn;k_W;2683190;1248066;408",
        "{4};Matterhorn;offiziell;Endonym;Alpiner Gipfel;k_W;2617049;1091673;4478",
        "{7};Rheinfall;offiziell;einfacher Name;Wasserfall;k_W;2688390;1281451;376",
        "{8};Blausee;offiziell;einfacher Name;Ort;< 20;2682500;1246600;420",
        "{9};Blausee;offiziell;einfacher Name;Quartier;20 bis 49;2682700;1246800;425",
        "{10};Uetliberg;offiziell;einfacher Name;Huegel;k_W;2679589;1244824;870",
        "{11};Uetliberg;offiziell;einfacher Name;Ort;20 bis 49;2679373;1245106;842",
        "{12};Lindenhof;offiziell;einfacher Name;Quartierteil;1'000 bis 1'999;2683200;1247500;428",
        "{15};Moos;offiziell;einfacher Name;Ort;100 bis 999;2687000;1252000;440",
        "{16};Moos;offiziell;einfacher Name;Ort;100 bis 999;2685500;1250000;450",
        "{19};Seeli;offiziell;einfacher Name;Ort;< 20;2699275;1252173;600",
        "{20};Seeli;offiziell;einfacher Name;Ort;< 20;2783000;1188000;1600",
        "{21};Zürich;offiziell;Endonym;Ort;> 100'000;2683500;1247184;418",
        "{22};Zurich;offiziell;Exonym;Ort;> 100'000;2683500;1247184;418",
        "{23};Wil SG;offiziell;einfacher Name;Ort;10'000 bis 49'999;2721323;1258457;583",
        "{24};Wil;offiziell;einfacher Name;Quartier;2'000 bis 9'999;2688988;1249443;440",
        "{25};Wil;offiziell;einfacher Name;Quartier;100 bis 999;2672161;1201260;459",
        "{26};Biel/Bienne;offiziell;Namenspaar;Ort;50'000 bis 100'000;2585470;1220617;434",
        "{34};Biel/Bienne;offiziell;Namenspaar;Haltestelle Bahn;k_W;2585140;1220206;437",
        "{27};Genève;offiziell;Endonym;Ort;> 100'000;2500226;1118241;376",
        "{28};Genf;offiziell;Exonym;Ort;> 100'000;2500226;1118241;376",
        "{29};Davos Dorf;offiziell;einfacher Name;Ort;2'000 bis 9'999;2783248;1187061;1564",
        "{30};Davos Platz;offiziell;einfacher Name;Ort;2'000 bis 9'999;2781852;1185223;1558",
        "{31};Küsnacht Im Holletsmoos 300/50/25m;offiziell;Endonym;Offenes Gebaeude;k_W;2688055;1240889;542",
        "{32};Flims Dorf;offiziell;einfacher Name;Ort;1'000 bis 1'999;2740808;1188878;1079",
        "{33};Flums;offiziell;einfacher Name;Ort;2'000 bis 9'999;2744605;1217332;454",
        "{35};Grindelwald;offiziell;einfacher Name;Ort;2'000 bis 9'999;2645921;1163748;1040",
        "{36};Grindelwald;offiziell;einfacher Name;Ort;< 20;2649992;1209309;892",
    ),
    "swissNAMES3D_PLY.csv": (
        "{5};Oerlikon;offiziell;einfacher Name;Quartier;10'000 bis 49'999;2683539;1251401;448",
        "{6};Zürichsee;offiziell;Endonym;See;k_W;2698596;1230913;406",
        "{14};Blausee;offiziell;einfacher Name;See;k_W;2687500;1252500;445",
    ),
    "swissNAMES3D_LIN.csv": (),
}
PLACE_NAMES_EDITION_YEARS = (2025, 2026)


def build_place_names_archive() -> bytes:
    """Build the zip archive of an edition the way swisstopo publishes it, each file with a byte order mark."""
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        for file_name, rows in PLACE_NAMES_FILE_TO_ROWS.items():
            lines = [PLACE_NAMES_COLUMNS, *rows]
            archive.writestr(file_name, ("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
    return archive_bytes.getvalue()


def build_place_names_editions() -> dict:
    """Build the catalogue answer that lists the editions, each with its archives in several formats."""
    editions = []
    for year in PLACE_NAMES_EDITION_YEARS:
        edition_id = f"swissnames3d_{year}"
        assets = {}
        for suffix in ("_2056.csv.zip", "_2056.shp.zip"):
            assets[f"{edition_id}{suffix}"] = {"href": f"https://example.test/{edition_id}/{edition_id}{suffix}"}
        editions.append({"id": edition_id, "assets": assets})
    return {"features": editions}


# Areas as squares in LV95, the corners east from, east to, north from, north to, around the places
# above: Zürich holds Lindenhof and the Blausee place and district, the Greifensee, an area of the canton
# itself, the Blausee lake, and Vaduz, in Liechtenstein, has no canton number. The real tables have the
# same columns, and more.
BOUNDARY_AREAS = (
    ("Zürich", "Gemeindegebiet", 1, (2676000.0, 2690000.0, 1241000.0, 1250000.0)),
    ("Greifensee", "Kantonsgebiet", 1, (2685000.0, 2695000.0, 1250000.0, 1256000.0)),
    ("Vaduz", "Gemeindegebiet", None, (2756000.0, 2760000.0, 1220000.0, 1225000.0)),
)
BOUNDARY_CANTONS = ((1, "Zürich"),)
BOUNDARY_EDITION_IDS = ("swissboundaries3d_2025-04", "swissboundaries3d_2026-01")
# The member name of the real archives
BOUNDARY_GEOPACKAGE_NAME = "swissBOUNDARIES3D_1_5_LV95_LN02.gpkg"
# The parts of a GeoPackage geometry the fake one uses: the header flags for little-endian without an
# envelope, the spatial reference of LV95, and the WKB types of a polygon and a multipolygon with heights
GEOPACKAGE_FLAGS_LITTLE_ENDIAN = 0b00000001
LV95_SPATIAL_REFERENCE = 2056
WKB_LITTLE_ENDIAN = 1
WKB_POLYGON_WITH_HEIGHTS = 1003
WKB_MULTIPOLYGON_WITH_HEIGHTS = 1006
# The server skips the heights, so every corner has the same
FAKE_HEIGHT_M = 500.0


def build_square_geometry(east_from_m: float, east_to_m: float, north_from_m: float, north_to_m: float) -> bytes:
    """Build a GeoPackage geometry of one square, a multipolygon with heights like the real areas."""
    corners = [
        (east_from_m, north_from_m),
        (east_to_m, north_from_m),
        (east_to_m, north_to_m),
        (east_from_m, north_to_m),
        (east_from_m, north_from_m),
    ]
    # The magic "GP", version 0, the flags and the spatial reference
    magic_version_and_flags = b"GP" + bytes([0, GEOPACKAGE_FLAGS_LITTLE_ENDIAN])
    spatial_reference = struct.pack("<i", LV95_SPATIAL_REFERENCE)
    header = magic_version_and_flags + spatial_reference

    packed_corners = [struct.pack("<3d", east, north, FAKE_HEIGHT_M) for east, north in corners]
    corner_count = len(corners)
    packed_corner_count = struct.pack("<I", corner_count)
    ring = packed_corner_count + b"".join(packed_corners)
    # One ring in the polygon, and one polygon in the multipolygon
    polygon = struct.pack("<BII", WKB_LITTLE_ENDIAN, WKB_POLYGON_WITH_HEIGHTS, 1) + ring
    multipolygon = struct.pack("<BII", WKB_LITTLE_ENDIAN, WKB_MULTIPOLYGON_WITH_HEIGHTS, 1) + polygon
    return header + multipolygon


def build_boundaries_geopackage(path) -> None:
    """Write a GeoPackage with the tables, spatial index and columns the server reads."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE tlm_kantonsgebiet (id INTEGER PRIMARY KEY, kantonsnummer INTEGER, name TEXT)")
    connection.execute(
        "CREATE TABLE tlm_hoheitsgebiet (id INTEGER PRIMARY KEY, geom BLOB, kantonsnummer INTEGER, name TEXT, "
        "objektart TEXT)"
    )
    connection.execute("CREATE VIRTUAL TABLE rtree_tlm_hoheitsgebiet_geom USING rtree(id, minx, maxx, miny, maxy)")

    for number, name in BOUNDARY_CANTONS:
        connection.execute("INSERT INTO tlm_kantonsgebiet (kantonsnummer, name) VALUES (?, ?)", (number, name))
    for row_id, (name, kind, canton_number, corners) in enumerate(BOUNDARY_AREAS, start=1):
        geometry = build_square_geometry(*corners)
        connection.execute(
            "INSERT INTO tlm_hoheitsgebiet VALUES (?, ?, ?, ?, ?)",
            (row_id, geometry, canton_number, name, kind),
        )
        connection.execute("INSERT INTO rtree_tlm_hoheitsgebiet_geom VALUES (?, ?, ?, ?, ?)", (row_id, *corners))
    connection.commit()
    connection.close()


def build_boundaries_archive(directory) -> bytes:
    """Build the zip archive of an edition's GeoPackage the way swisstopo publishes it."""
    geopackage_file = directory / BOUNDARY_GEOPACKAGE_NAME
    if not geopackage_file.exists():
        build_boundaries_geopackage(geopackage_file)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.write(geopackage_file, BOUNDARY_GEOPACKAGE_NAME)
    return archive_bytes.getvalue()


def build_boundaries_editions() -> dict:
    """Build the catalogue answer that lists the editions, each with its archives in several formats."""
    editions = []
    for edition_id in BOUNDARY_EDITION_IDS:
        assets = {}
        for suffix in ("_2056_5728.gpkg.zip", "_2056_5728.shp.zip"):
            assets[f"{edition_id}{suffix}"] = {"href": f"https://example.test/{edition_id}/{edition_id}{suffix}"}
        editions.append({"id": edition_id, "assets": assets})
    return {"features": editions}
