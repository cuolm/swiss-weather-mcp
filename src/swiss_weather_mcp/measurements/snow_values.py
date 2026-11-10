"""The snow values both measurement sources return, so the snow tool compares their stations alike."""
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple, Optional, Union

if TYPE_CHECKING:
    # Only for the type checker, as both sources import this module
    from .meteoswiss_source import MeteoSwissStation
    from .slf_source import SLFStation

# A station that measures snow, of either source
SnowStation = Union["MeteoSwissStation", "SLFStation"]


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
