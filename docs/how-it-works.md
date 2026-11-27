# How It Works

## Times
- The tools take Swiss local time in ISO 8601, such as `"2026-09-23T14:00"`. The server handles summer
  and winter time.
- Answers give times with their UTC offset, such as `2026-09-23T13:00+02:00`.
- An hourly value stamped `14:00` covers 13:00 to 14:00. Cloud cover and the freezing level are the
  value at that moment, not over the hour.
- The forecast covers today and the next 8 days. `current_date_and_time()` gives today's date, which
  the model needs for questions such as "tomorrow" or "tonight".

## Locations
- A location is a place name from swissNAMES3D: a town, village, city quarter, peak, pass, lake,
  glacier, valley or railway station, such as `Zürich`, `Oerlikon`, `Titlis` or `Zürichsee`.
- Case and accents don't matter, and a few English names such as `Geneva` work too.
- When several places share a name and `near` is missing, the tool takes the biggest one if that is
  clear: it has 1,000 or more people and every other place is a settlement of fewer than 100. For example,
  `Grindelwald` is the name of the village in canton Bern (2,000 to 9,999 people) and of a hamlet in
  canton Luzern (fewer than 20 people), so the tool takes the village. A peak, lake or pass of the same
  name is never skipped.
- Otherwise, a shared name such as `Blausee` needs `near`: a village next to the one the user means,
  such as `near="Riederalp"`. Without it, the tool lists all places of the name, the biggest first,
  each with the call that chooses it and the municipality and canton it lies in:
  ```
  'Blausee' is the name of 6 places. [...] The biggest first:
    location="Blausee", near="Blausee-Mitholz": quarter and lake, fewer than 20 people, 887 m, municipality Kandergrund, canton Bern
    location="Blausee", near="Ramiswil": settlement, fewer than 20 people, 683 m, municipality Mümliswil-Ramiswil, canton Solothurn
    ...
  ```
- The municipalities and cantons use swisstopo's names, in the local language, such as `Genève`
  or `Graubünden`. The cantons of the stations use the same names.
- A name no place has can be the name of a municipality from swissBOUNDARIES3D, such as `Davos`. The
  tool then takes the biggest settlement inside the municipality, such as `Jona` for `Rapperswil-Jona`.
  - When several are of the same size and lie within 3 km of each other, counting 100 m of height gap
    like 2 km, they have the same weather, and the tool takes the first by name: `Davos Dorf` of
    `Davos Dorf` and `Davos Platz`.
  - When they lie farther apart, the tool lists them like the places of a shared name, each with its
    own name as `near`, such as `location="Anniviers", near="Zinal"` for one of the ten villages of
    `Anniviers`, from Niouc at 883 m to Chandolin at 1950 m.
  - Tested on one forecast run, of 2026-11-25: of the 94 municipalities with several settlements of
    the same size, the 44 whose settlements lie within 3 km differed by at most 1 °C.
- An unknown name gets up to 10 suggestions, such as `Grosser Aletschgletscher` for `Aletschgletscher`.

## Forecast Points
- The forecast comes from a MeteoSwiss forecast point near the place. An answer names it in
  data_from when it is more than 1 km away or more than 50 m higher or lower.
- Temperature changes faster with height than with distance: in the standard atmosphere (ISO 2533),
  air cools by 0.65 °C per 100 m.
- So among the points within 5 km, the server takes the one with the smallest distance plus 20 times
  the height gap: 100 m of height gap counts like 2 km of distance. Without a point within 5 km, it
  takes the nearest one.
- Why 20: the bigger the factor, the more a point at the place's height wins over a nearer point at
  another height. Tested on the real data, factors up to 20 make the height gaps of lakes and peaks much
  smaller. At 20, every town of 1,000 people or more still gets a point within 3 km.

## Forecast Values Are Medians
- Temperature and rain are the median of the many possible outcomes MeteoSwiss calculates, not the
  most likely value.
- Medians do not add up: when showers are possible but unlikely in any 3-hour block, every block shows
  0 mm while the day's median is above 0.
- So for the rain of a whole day, use `forecast_daily`, not the sum of the `forecast_rain` blocks.

## Forecasts
- `forecast_daily` gives 1 to 9 days in a row, each a Swiss calendar day from 00:00 to 24:00.
  - The lowest and highest temperature are the lowest and highest hourly mean of the day, and the
    weather words describe the daytime.
  - The rain comes with its 10th and 90th percentile: in 8 of 10 possible outcomes, the day's rain lies
    between them. A value MeteoSwiss does not publish for the place is None.
  - Example: `forecast_daily("Braunwald", "2026-11-21", 2)`, for a weekend.
- `forecast_rain` gives 3-hour blocks over a period: the chance of rain, the median rainfall, how much
  the wettest hour may bring (its 90th percentile) and the weather in words.
  - The median is often 0 when showers are possible, so also look at the chance and the "up to" amount.
  - Near the end of the forecast, a period ends with the last full 3-hour block.
  - Example: `forecast_rain("Braunwald", "2026-11-21T12:00", "2026-11-21T18:00")`, for the afternoon.
- `forecast_temperature` gives the mean temperature of each hour over a period, or of a single hour
  when no end is given.
  - Each hour comes with its 10th and 90th percentile: in 8 of 10 possible outcomes, the temperature
    lies between them.
  - Example: `forecast_temperature("Braunwald", "2026-11-21T15:00")`, for one hour.
- `forecast_sunshine` gives the sunshine hours over a period, or in a single hour when no end is given.
  - Example: `forecast_sunshine("Braunwald", "2026-11-21T12:00", "2026-11-21T18:00")`, for the afternoon.
- `forecast_wind` gives the mean speed over the hour up to a time, the strongest one-second gust in that
  hour and the direction.
  - The gust comes with its 90th percentile: in 9 of 10 possible outcomes the gust stays below it, so it
    is a cautious high estimate, not the highest gust possible.
  - The direction is where the wind blows from, in degrees clockwise from north and as a compass point.
  - Example: `forecast_wind("Braunwald", "2026-11-21T14:00")`.
- `forecast_cloud_cover` gives the low, medium and high cloud at a time, and a total.
  - MeteoSwiss publishes the layers separately and they overlap, so the total is an estimate that
    assumes the layers are independent.
  - Example: `forecast_cloud_cover("Braunwald", "2026-11-21T14:00")`.
- `forecast_freezing_level` gives the height of the 0 °C line above sea level at a time.
  - Example: `forecast_freezing_level("Braunwald", "2026-11-21T14:00")`.

## Measurements
- `measurements_current` uses one of the about 160 MeteoSwiss weather stations (SwissMetNet). It takes
  the first of the 3 best stations, by distance plus 20 times the height gap as for the forecast points,
  that sent a temperature in the last hour. There are far fewer stations than forecast points, so the
  station can be far from the place and much higher or lower.
  - Every answer starts with a sentence that says so, such as "The measurements for Braunwald (1290 m)
    are those of the MeteoSwiss station Elm (958 m), 13.5 km away and 332 m lower." As for the forecast
    points, a station at most 1 km away and 50 m higher or lower counts as the place itself, so it is not
    named.
  - It gives the temperature, humidity, dew point, rain and sunshine of the last 10 minutes, the wind,
    gust, wind direction and sea-level pressure. It also gives how the temperature and pressure changed
    over the last 3 hours, and the rain, sunshine and strongest gust of the last hour.
  - In the mountains it adds an `slf_station` block from one of the about 200 mountain stations (IMIS)
    of the [SLF](https://www.slf.ch), most of them between 1300 and 3300 m. The block gives the
    temperature, humidity, wind, wind direction and strongest 5-second gust of the 30 minutes up to the
    station's latest measurement. With at least 10 cm of snow, it also gives the temperature of the snow
    surface over the same 30 minutes.
  - The block is added only when an SLF station is closer to the place than the MeteoSwiss station, by
    the same measure, and it sent a temperature in the last hour. Up to 3 SLF stations are tried. On the
    real data, this happens for no place below 800 m and for most places above 2000 m. The block names its
    station, its distance and height. When SLF cannot be reached, the answer comes without the block.
  - Example: `measurements_current("Braunwald")`.
- `measurements_history` gives the daily values of a MeteoSwiss station, chosen as for
  `measurements_current`: the first of the 3 best stations that has a temperature on the days asked.
  - A day runs from 00:00 to 24:00 UTC, which is 01:00 to 01:00 Swiss time in winter and 02:00 to 02:00
    in summer. MeteoSwiss publishes a day the next morning.
  - The days go back to when the station started, such as 1864 for Zürich / Fluntern. A value the
    station did not measure is None, such as the gust at Zürich / Fluntern before 1981. Days before this
    year come from a file of up to about 6.4 MB per station.
  - Normally a day's rain covers the same UTC day as the other values. Many stations only started
    measuring it that way later: 87 of the 140 stations with rain started in 1981 or in the 2010s,
    Zürich / Fluntern in 1981. For days before that, the answer gives the rain from 06:00 UTC to
    06:00 UTC the next day instead, as rain_06_to_06_utc_mm.
  - It also gives the snow of each day in a separate block, from one of three kinds of station: one of
    the 247 sites where a MeteoSwiss observer measures each morning, half of them since 1971 or earlier;
    one of about 100 SLF manual sites, where an observer measures too, at Davos Flüelastr. since 1945; or
    one of about 130 automatic SLF stations, at Bärentälli since 1998. The block gives the snow depth at
    06:00 UTC and the new snow of the 24 hours after it.
  - At an automatic SLF station, the snow depth is the median of the 24 hours up to 06:00 UTC, and the
    new snow is calculated by SLF from the snow depth.
  - The snow station is the first with snow values on the days asked, among the 3 best MeteoSwiss sites
    and the 3 best SLF stations within the 25 km limit of `measurements_snow`, see below. They are tried
    in order of distance plus 20 times the height gap. Each kind has its own 3 tries, because many SLF
    stations started after the days asked, and most SLF observers do not measure in summer. Only the
    stations that SLF lists as active are used.
  - The block is null when no station is close enough. For the 5817 forecast points, that is the case
    for fewer than 1 % below 1200 m, for 2 % between 1200 and 2000 m and for 21 % above.
  - `values="weather"` or `values="snow"` returns only one of the two blocks.
  - Example: `measurements_history("Braunwald", "2025-01-01", 7, "snow")`, for only the snow of a week.
- `measurements_snow` gives the snow depth and the new snow, each from its own station: the first of the
  3 best stations that has a current value. Each value names its station, its distance and height, unless
  the station is next to the place.
  - The snow depth comes from one of about 75 MeteoSwiss stations, one of about 130 automatic SLF
    stations or one of about 100 SLF manual sites, where an observer measures the snow by hand each
    morning from November to April. A depth that a station measures every 10 or 30 minutes is used for up
    to 210 minutes, as on
    [SLF's map](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/measured-values/information-about-snow-measurement/).
    An observer's depth is used until the next morning.
  - The new snow comes only from SLF. An observer measures it, or SLF calculates it from the snow depth
    at an automatic station, because old snow settles while new snow falls. Both cover the 24 hours up to
    about 06:00 UTC. The value for 3 days is the sum of three 24-hour values, as on SLF's maps.
  - SLF says the observers' values are very accurate, but most manual sites lie below about 2000 m. With
    wet snow or rain, there is more new snow higher up.
  - Snow changes with height and terrain, so only stations whose distance plus 20 times the height gap
    is at most 25 km are used, such as a station 15 km away and 500 m higher. Tested on the 5817 forecast
    points: 94 % of those between 1200 and 2000 m get a new snow station. Below 800 m, where the nearest
    SLF station is often 40 km away and 600 m higher, 88 % get no new snow station, but 96 % still get a
    snow depth.
  - A block is null when none of its stations has a current value. When both blocks are null, the tool
    says so.
  - Example: `measurements_snow("Braunwald")`.
- `measurements_extremes` lists the 5 warmest, coldest or windiest stations.
  - For the wettest, it lists the 5 wettest stations and counts the stations with rain in Switzerland
    and in each canton.
  - For the sunniest, it groups the stations by how many of the last 10 minutes had sunshine, with the
    terms of [MeteoSwiss](https://www.meteoswiss.admin.ch/weather/weather-and-climate-from-a-to-z/sunshine.html):
    mostly sunny (8 to 10 minutes, 80 to 100 %), rather sunny (5 to 7), partly sunny (2 to 4), only a
    little sunshine (1) and hardly any sunshine (0). MeteoSwiss's ranges meet at 20, 50 and 80 %. A
    station exactly on such a border gets the sunnier term, so 2 minutes (20 %) is partly sunny. The
    cantons are sorted by the average sunshine of their stations.
  - For the deepest snow and the most new snow, it lists the 5 SLF stations with the most snow, and
    counts the stations with snow among those that measured. The values are of 06:00 UTC on the newest
    morning of SLF's daily snow: those of about 130 automatic stations, and of the manual sites whose
    observer measured that morning. MeteoSwiss publishes the snow of each station in its own file, so
    its stations are not ranked. The new snow is of the 24 hours up to 06:00 UTC. SLF calculates it
    for its automatic stations from the snow depth with its SNOWPACK model, while observers measure it
    at the manual sites, so each station says which kind it is. The deepest snow is mostly at the
    highest automatic stations: on 2026-11-17 the first 5 lay between 2252 and 2800 m.
  - Example: `measurements_extremes("sunniest")`.

## Avalanche Bulletin
- `avalanche_bulletin` gives the [SLF avalanche bulletin](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/)
  valid now. In winter SLF publishes it every day at 17:00, valid until 17:00 the next day, with an update
  at 08:00 when needed; in summer and autumn only after heavy snowfall. When none is valid, the tool says so.
  - SLF groups its warning regions with the same danger into areas, and each area is one bulletin with an
    outline. The tool takes the bulletin whose outline the place lies in. The bulletin covers the Alps and
    the Jura, never the Plateau, so for a place such as Zürich the tool says it lies in no region.
  - The danger level goes from 1 (low) to 5 (very high). From level 2 up, SLF adds a
    [subdivision](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/danger-levels/):
    `-` for the lower part of the level, `=` for the middle and `+` for the upper part, such as
    `considerable (3-)`. In spring, a second level, `danger_level_later`, gives the danger from wet snow
    later in the day.
  - Each avalanche problem, such as wind slab, has its own danger level, heights and aspects (the
    directions the slopes face). The answer keeps every problem, also those that apply only higher up,
    because people often go higher than the place they ask about. It gives the place's altitude so the
    heights can be compared.
  - Each problem comes with SLF's summary and description. The snowpack, outlook, weather forecast and
    weather review are SLF's texts for all of Switzerland; the weather texts are missing on some days.
  - The bulletins, about 80 KB, are downloaded again when the cached copy is older than 10 minutes.
  - Every answer names SLF as its source, with a link, as SLF asks, and links SLF's map of the bulletin.
  - Example: `avalanche_bulletin("Braunwald")`.

## Sun and Moon
- `astronomy_sun` gives sunrise, sunset and when civil twilight begins and ends on a day.
  - Civil twilight is the light before sunrise and after sunset; outside it, artificial light is
    normally needed outdoors.
  - The times are for a level horizon at sea level, so in a valley the Sun appears later behind the
    mountains.
  - The position of the Sun follows Van Flandern and Pulkkinen (1979), and sunrise, sunset and civil
    twilight follow the USNO definitions: the centre of the Sun at -0.8333° and -6° altitude.
  - Example: `astronomy_sun("Braunwald", "2026-11-21")`.
- `astronomy_moon` gives moonrise and moonset on a day, the Moon's phase and how much of it is lit.
  - Moonrise or moonset is None when the Moon does not rise or set that day.
  - The day has a moon_phase_time when New Moon, First Quarter, Full Moon or Last Quarter happens on it.
  - The lit percent is the one at 12:00. The times are for a level horizon, as for the Sun.
  - Example: `astronomy_moon("Braunwald", "2026-11-21")`.

## Cache and Downloads
- The cache lives in your OS cache directory, such as `~/Library/Caches/swiss-weather-mcp` on macOS or
  `~/.cache/swiss-weather-mcp` on Linux. `SWISS_WEATHER_MCP_CACHE_DIR` moves it elsewhere.
- The first start downloads the swissNAMES3D archive (about 33 MB) and the swissBOUNDARIES3D GeoPackage
  (about 37 MB, kept unpacked at about 74 MB).
- MeteoSwiss publishes a new forecast run every hour, each hourly parameter as one file of about 30 to
  34 MB for all forecast points. By default the server downloads such a file for every new forecast
  point and keeps only that point's rows.
- With `--cache-all-forecast-points` it keeps the whole files, so further places in the same run need no
  new download. This helps when many places are asked within the hour, at the cost of disk space.
- The server keeps every downloaded file and uses it again until it is too old. All users share the copy:

  | Data | Downloaded again after |
  |------|------------------------|
  | Places (swissNAMES3D) and municipalities (swissBOUNDARIES3D) | a new edition, checked every 30 days |
  | Forecast point table, station tables and lists | 7 days |
  | Forecast values | a new model run, checked every 5 minutes |
  | Current measurements (MeteoSwiss and SLF) | 5 minutes |
  | Avalanche bulletin | 10 minutes |
  | Recent days and SLF daily snow | 1 hour |
  | SLF snow history files | 1 day |
  | Older days | 30 days |

- Places, tables, lists and the forecast point table are also kept in memory after the first read, and
  the edition of the municipalities is checked once at start. A running server reads them again only
  after a restart, so for them the times above apply only when the server starts.
- A file is first written under a temporary name and moved into place when complete, so a cut download
  is never used.
- When several users need the same missing file at the same time, only one downloads it and the others
  wait for it. Each file has its own lock, so different files still download at the same time. This holds
  within one server process.
- When a download fails, files that change slowly, such as station tables and past days, are taken
  from the older copy. Current measurements and the avalanche bulletin are not.
