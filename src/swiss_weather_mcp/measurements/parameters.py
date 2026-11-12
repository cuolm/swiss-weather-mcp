"""
The parameters the server reads from the SwissMetNet measurements and the manual precipitation stations.

The comment on each code is the MeteoSwiss description of that parameter, from
ogd-smn_meta_parameters.csv and ogd-nime_meta_parameters.csv. Times are UTC. A "current value" is measured at its timestamp, and a
"ten minutes" value covers the 10 minutes up to its timestamp. A daily value covers the UTC day
that starts at its timestamp.
"""

TEMPERATURE = "tre200s0"         # Air temperature 2 m above ground; current value [°C]
HUMIDITY = "ure200s0"            # Relative air humidity 2 m above ground; current value [%]
DEW_POINT = "tde200s0"           # Dew point 2 m above ground; current value [°C]
PRECIPITATION = "rre150z0"       # Precipitation; ten minutes total [mm]
SUNSHINE = "sre000z0"            # Sunshine duration; ten minutes total [min]
WIND_SPEED = "fu3010z0"          # Wind speed; ten minutes mean in km/h
WIND_GUSTS = "fu3010z1"          # Gust peak (one second); maximum in km/h
WIND_DIRECTION = "dkl010z0"      # Wind direction; ten minutes mean [°]
PRESSURE_SEA_LEVEL = "pp0qnhs0"  # Pressure reduced to sea level according to standard atmosphere (QNH); current value [hPa]
SNOW_DEPTH = "htoauts0"          # Snow depth (automatic measurement); current value [cm]

# Every column the server reads from a station's now file and from the all stations file
ALL_NOW_PARAMETERS = (
    TEMPERATURE, HUMIDITY, DEW_POINT, PRECIPITATION, SUNSHINE, WIND_SPEED, WIND_GUSTS,
    WIND_DIRECTION, PRESSURE_SEA_LEVEL,
)
# The now files also hold the snow depth, which the all stations file does not
NOW_FILE_PARAMETERS = (*ALL_NOW_PARAMETERS, SNOW_DEPTH)

# ── daily, 00:00 - 24:00 UTC ─────────────────────────────────────────────────
TEMPERATURE_DAY_MEAN = "tre200d0"   # Air temperature 2 m above ground; daily mean [°C]
TEMPERATURE_DAY_MIN = "tre200dn"    # Air temperature 2 m above ground; daily minimum [°C]
TEMPERATURE_DAY_MAX = "tre200dx"    # Air temperature 2 m above ground; daily maximum [°C]
PRECIPITATION_DAY = "rka150d0"      # Precipitation; daily total 0 UTC - 0 UTC [mm]
SUNSHINE_DAY = "sre000d0"           # Sunshine duration; daily total [min]
WIND_GUSTS_DAY = "fu3010d1"         # Gust peak (one second); daily maximum in km/h
# Goes back further than the rain from 0 to 0 UTC, which starts in 1981 or later
PRECIPITATION_DAY_6_TO_6_UTC = "rre150d0"  # Precipitation; daily total 6 UTC - 6 UTC following day [mm]

# Every column the server reads from a station's daily files
ALL_DAILY_PARAMETERS = (
    TEMPERATURE_DAY_MEAN, TEMPERATURE_DAY_MIN, TEMPERATURE_DAY_MAX, PRECIPITATION_DAY, SUNSHINE_DAY,
    WIND_GUSTS_DAY, PRECIPITATION_DAY_6_TO_6_UTC,
)

# ── daily, manual precipitation stations ─────────────────────────────────────
SNOW_DEPTH_DAY = "hto000d0"  # Snow depth; morning measurement at 6 UTC [cm]
NEW_SNOW_DAY = "hns000d0"    # Fresh snow; daily total 6 UTC - 6 UTC following day [cm]

# Every column the server reads from a manual site's daily files
ALL_MANUAL_SITE_DAILY_PARAMETERS = (SNOW_DEPTH_DAY, NEW_SNOW_DAY)
