"""The snow values both measurement sources return, so the snow tool compares their stations alike."""
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Optional, Union

if TYPE_CHECKING:
    # Only for the type checker, as both sources import this module
    from .meteoswiss_source import MeteoSwissStation
    from .slf_source import SLFStation

# A station that measures snow, of either source
SnowStation = Union["MeteoSwissStation", "SLFStation"]

# How old a current snow depth may be. SLF's map of measured values shows a station's latest value for up
# to 210 minutes, and snow depth changes slowly,
# https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/measured-values/information-about-snow-measurement/
SNOW_DEPTH_MAX_AGE = timedelta(minutes=210)


class SnowDepth(NamedTuple):
    """The snow depth of a station and when it was measured."""
    station: SnowStation
    measured_at: datetime
    snow_depth_cm: float


class NewSnow(NamedTuple):
    """The new snow of an SLF station in the 24 hours and in the 3 days up to measured_at."""
    station: "SLFStation"
    measured_at: datetime
    new_snow_24_hours_cm: float
    new_snow_3_days_cm: Optional[float]  # None when one of the 3 days has no value
