"""
The parameters the server reads from the latest SwissMetNet measurements.

The comment on each code is the MeteoSwiss description of that parameter, from
ogd-smn_meta_parameters.csv. Times are UTC. A "current value" is measured at its timestamp, and a
"ten minutes" value covers the 10 minutes up to its timestamp.
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

# Every column the server reads from a station's now file and from the all stations file
ALL_PARAMETERS = (
    TEMPERATURE, HUMIDITY, DEW_POINT, PRECIPITATION, SUNSHINE, WIND_SPEED, WIND_GUSTS,
    WIND_DIRECTION, PRESSURE_SEA_LEVEL,
)
