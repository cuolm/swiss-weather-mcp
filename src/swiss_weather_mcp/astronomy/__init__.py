"""
The Sun and the Moon over a place: when they rise and set, the civil twilight, and the Moon's
phase and how much of it is lit.

How the answers are calculated:

From the paper of Van Flandern and Pulkkinen: where the Sun and the Moon are on the sky
1. Time (sky.py): the moment becomes the number of days since 1 January 2000 at 12:00, and the
   number of centuries since 1900. Just counts of time.
2. Average motion (sky.py): angles that grow at a steady speed, such as the average position of
   the Moon along its path. Not yet the real position.
3. Corrections (series.py): the real position differs from the average one by a small part that
   swings back and forth. The paper gives it as long sums of sine and cosine terms, added up for
   the moment.
4. Position (moon.py, sun.py): where the Sun or the Moon stands among the stars, seen from the
   centre of the Earth, as right ascension and declination, the sky's version of longitude and
   latitude. Also the distance of the Moon, which moonrise needs, and of the Sun, which only the
   lit part of the Moon needs.

From USNO: from the centre of the Earth to the place, and when an event happens
5. How high in the sky (sky.py): the position of step 4 is fixed to the stars, but the Earth
   turns. How far it has turned at that moment, with the place's longitude and latitude, gives
   how high the Sun or the Moon appears in the sky for someone standing there, as an angle
   above the horizon: 0° on the horizon, 90° straight overhead, below 0° under the horizon.
6. Rise and set (sky.py, moon.py): the moments that angle passes the value USNO defines:
   - sunrise and sunset: the middle of the Sun 0.8333° below the horizon, when its top edge seems
     to touch the horizon, as the air bends its light upwards
   - civil twilight: the middle of the Sun 6° below the horizon; outside it, artificial light is
     normally needed outdoors
   - moonrise and moonset: the middle of the Moon 0.5666° plus its radius below the horizon,
     minus its parallax: seen from the place on the Earth's surface, not from the centre, the
     near Moon appears up to about 1° lower. USNO puts this into the angle instead of moving
     the position; the Sun is so far away that its sunrise angle leaves it out.
7. Phase (moon.py): how far the Moon is ahead of the Sun along the Sun's yearly path across the
   sky. 0° is New Moon, 90° First Quarter, 180° Full Moon and 270° Last Quarter.

From the Explanatory Supplement to the Astronomical Almanac
8. Lit part (moon.py): 100 (1 + cos i) / 2, with i the angle at the Moon between the Sun and the
   Earth. Also the radius of the Moon, 1738 km, and of the Earth, 6378.140 km, for the Moon's
   size in step 6.

Our own choices: the day is checked hour by hour and then narrowed down to the second, the times
are rounded to the minute, and the lit percent is the one at 12:00.

Like USNO, the times are for a level horizon at sea level, so in a valley the Sun and the Moon
appear later.

Sources:
- T. C. Van Flandern and K. F. Pulkkinen, "Low-precision formulae for planetary positions",
  The Astrophysical Journal Supplement Series 41, 391-411 (1979),
  https://ui.adsabs.harvard.edu/abs/1979ApJS...41..391V/abstract
- USNO, Astronomical Applications Department:
  "Computing Approximate Sidereal Time", https://aa.usno.navy.mil/faq/GAST
  "Computing Altitude and Azimuth", https://aa.usno.navy.mil/faq/alt_az
  "Rise, Set, and Twilight Definitions", https://aa.usno.navy.mil/faq/RST_defs
  "Phases of the Moon and Percent of the Moon Illuminated", https://aa.usno.navy.mil/faq/moon_phases
  Glossary of The Astronomical Almanac, https://aa.usno.navy.mil/faq/asa_glossary
- P. K. Seidelmann (ed.), Explanatory Supplement to the Astronomical Almanac (1992),
  https://archive.org/details/131123ExplanatorySupplementAstronomicalAlmanac
"""
