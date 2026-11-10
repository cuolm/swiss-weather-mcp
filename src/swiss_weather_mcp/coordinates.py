"""
Turn positions between WGS84 latitude and longitude and LV95, the Swiss grid in metres.

The swisstopo approximate formulas of "Approximate formulas for the transformation between Swiss
projection coordinates and WGS84" (December 2016),
https://www.swisstopo.admin.ch/dam/en/sd-web/KLRCX9XIdXDu/ch1903wgs84-EN.pdf
From WGS84 to LV95, section 1, they are better than 1 metre everywhere in Switzerland; from LV95 to
WGS84, section 2, better than 0.12" in longitude and 0.08" in latitude.
"""
ARCSECONDS_PER_DEGREE = 3600
# Section 1 takes the angles relative to Bern in units of 10000"
BERN_LATITUDE_ARCSECONDS = 169028.66
BERN_LONGITUDE_ARCSECONDS = 26782.5
ARCSECONDS_PER_10000_ARCSECONDS = 10000
# Section 2 takes the position relative to Bern in units of 1000 km
BERN_EAST_M = 2600000
BERN_NORTH_M = 1200000
METRES_PER_1000_KM = 1000000
# Section 2 gives the angles in units of 10000"; one degree is 3600"
DEGREES_PER_10000_ARCSECONDS = 100 / 36


def _calculate_offset_from_bern(angle_deg: float, bern_angle_arcseconds: float) -> float:
    """Return how far an angle lies from Bern's, in the unit of the formulas, 10000"."""
    return (angle_deg * ARCSECONDS_PER_DEGREE - bern_angle_arcseconds) / ARCSECONDS_PER_10000_ARCSECONDS


def calculate_lv95_east_m(latitude_deg: float, longitude_deg: float) -> float:
    """Return the LV95 east coordinate of a WGS84 position."""
    latitude = _calculate_offset_from_bern(latitude_deg, BERN_LATITUDE_ARCSECONDS)
    longitude = _calculate_offset_from_bern(longitude_deg, BERN_LONGITUDE_ARCSECONDS)
    return (
        2600072.37
        + 211455.93 * longitude
        - 10938.51 * longitude * latitude
        - 0.36 * longitude * latitude**2
        - 44.54 * longitude**3
    )


def calculate_lv95_north_m(latitude_deg: float, longitude_deg: float) -> float:
    """Return the LV95 north coordinate of a WGS84 position."""
    latitude = _calculate_offset_from_bern(latitude_deg, BERN_LATITUDE_ARCSECONDS)
    longitude = _calculate_offset_from_bern(longitude_deg, BERN_LONGITUDE_ARCSECONDS)
    return (
        1200147.07
        + 308807.95 * latitude
        + 3745.25 * longitude**2
        + 76.63 * latitude**2
        - 194.56 * longitude**2 * latitude
        + 119.79 * latitude**3
    )


def calculate_wgs84_latitude_deg(east_m: float, north_m: float) -> float:
    """Return the WGS84 latitude in degrees, north positive, of an LV95 position."""
    east = (east_m - BERN_EAST_M) / METRES_PER_1000_KM
    north = (north_m - BERN_NORTH_M) / METRES_PER_1000_KM
    latitude = (
        16.9023892
        + 3.238272 * north
        - 0.270978 * east**2
        - 0.002528 * north**2
        - 0.0447 * east**2 * north
        - 0.0140 * north**3
    )
    return latitude * DEGREES_PER_10000_ARCSECONDS


def calculate_wgs84_longitude_deg(east_m: float, north_m: float) -> float:
    """Return the WGS84 longitude in degrees, east positive, of an LV95 position."""
    east = (east_m - BERN_EAST_M) / METRES_PER_1000_KM
    north = (north_m - BERN_NORTH_M) / METRES_PER_1000_KM
    longitude = (
        2.6779094
        + 4.728982 * east
        + 0.791484 * east * north
        + 0.1306 * east * north**2
        - 0.0436 * east**3
    )
    return longitude * DEGREES_PER_10000_ARCSECONDS
