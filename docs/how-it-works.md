# How It Works

## Times
- Times are Swiss clock time in ISO 8601, such as `"2026-09-23T14:00"`. The server applies summer or
  winter time.
- Answers give times with their UTC offset, such as `2026-09-23T13:00+02:00`.
- An hourly value stamped `14:00` covers 13:00 to 14:00. Cloud cover and the freezing level are values
  at that moment.
- The forecast covers today and the next 8 days. `current_date_and_time()` gives today's date, which
  questions such as "tomorrow" or "tonight" need.

## Locations
- A location is a place name from swissNAMES3D: a town, village, city district, peak, pass, lake,
  glacier, valley or railway station, such as `Zürich`, `Oerlikon`, `Titlis` or `Zürichsee`.
- Case and accents don't matter, and a few English names such as `Geneva` work too.
- A name of several places takes the biggest one without `near` when that is obvious: it has 1,000 or more
  people and every other place is a settlement of fewer than 100, such as the village `Grindelwald` and a
  hamlet in canton Luzern. A peak, lake or pass of the same name is never passed over.
- Any other name of several places, such as `Blausee`, needs `near`: a village next to the one the user means,
  such as `near="Riederalp"`. Without it, the tool lists all places of the name, the biggest first,
  each with the call that chooses it and the municipality and canton it lies in:
  ```
  'Blausee' is the name of 6 places. [...] The biggest first:
    location="Blausee", near="Blausee-Mitholz": district and lake, fewer than 20 people, 887 m, municipality Kandergrund, canton Bern
    location="Blausee", near="Ramiswil": settlement, fewer than 20 people, 683 m, municipality Mümliswil-Ramiswil, canton Solothurn
    ...
  ```
- The municipalities and cantons carry swisstopo's names, in the local language, such as `Genève`
  or `Graubünden`. They appear once the swissBOUNDARIES3D GeoPackage is downloaded in the
  background at start.
- An unknown name gets up to 10 suggestions, such as `Davos Dorf` and `Davos Platz` for `Davos`.

## Forecast Points
- The forecast comes from a MeteoSwiss forecast point near the place. An answer names it in
  data_from when it is more than 1 km away or more than 50 m higher or lower.
- Temperature changes faster with height than with distance: in the standard atmosphere (ISO 2533),
  air cools by 0.65 °C per 100 m.
- So among the points within 5 km, the server takes the one with the smallest distance plus 20 times
  the height gap: 100 m of height gap counts like 2 km of distance. Without a point within 5 km, it
  takes the nearest one.
- The factor 20 was tested on the real data: up to 20, the height gaps of lakes and peaks shrink a lot,
  and every town of 1,000 people or more still gets a point within 3 km.

## Measurements
- `measurements_current` uses one of the about 160 SwissMetNet stations: of the 3 with the smallest
  distance plus 20 times the height gap, the first that published a temperature in the last hour.
  Stations are far fewer than forecast points, so the station can still be far from the place and
  higher or lower.
- Every answer starts with a sentence that says so, such as "The measurements for Braunwald (1290 m)
  are those of the MeteoSwiss station Elm (958 m), 13.5 km away and 332 m lower." As for the forecast
  points, a station at most 1 km away and 50 m higher or lower stands for the place, so it is not named.
- It gives the temperature, humidity, dew point, rain and sunshine in the last 10 minutes, wind, gust,
  direction and sea-level pressure, plus the temperature and pressure change over the last 3 hours and
  the rain, sunshine and strongest gust of the last hour.
- In the mountains it adds an `slf_station` block from one of the about 200 IMIS stations of the
  [SLF](https://www.slf.ch), most between 1300 and 3300 m: the temperature, humidity, wind, direction
  and strongest 5-second gust of the 30 minutes up to its latest row. It is added only when the station's
  distance plus 20 times the height gap is smaller than the MeteoSwiss station's, and the station
  published a temperature in the last hour (of the 3 best, the first that did). On the real data that is
  no place below 800 m and most places above 2000 m. Its own data_from names the station, its distance
  and height. When SLF cannot be reached, the answer comes without the block.
- The block also gives the snow surface temperature of the 30 minutes, only with at least 10 cm of snow.
- `measurements_history` gives MeteoSwiss's daily values of the station chosen the same way: of the 3
  first stations, the first with a temperature on the days asked. A day runs from 00:00 to 24:00 UTC,
  which is 01:00 to 01:00 Swiss time in winter and 02:00 to 02:00 in summer, and MeteoSwiss publishes
  it the next morning. The days go back to when the station started, such as 1864 for Zürich /
  Fluntern, where the rain from 0 to 0 UTC and the gust start in 1981; a value the station did not
  measure is None. Before that, a day gives the rain from 06:00 to 06:00 UTC, as rain_06_to_06_utc_mm:
  the rain from 0 to 0 UTC starts in 1981 or the 2010s at 87 of the 140 stations with rain. Days before this year come from a file of up to about 6.4 MB per station.
- It also gives the snow of the days in a block of its own, from one of the 247 manual stations where a
  MeteoSwiss observer measures each morning, half of them since 1971 or earlier: the snow depth at
  06:00 UTC and the new snow of the 24 hours from then. The station is chosen as for
  `measurements_snow`: of the 3 best within 25 km by distance plus 20 times the height gap, the first
  with snow values on the days. The block is null when there is none. No manual station is close enough
  for fewer than 2 % of the 5817 forecast points below 1200 m, for 8 % between 1200 and 2000 m and for
  80 % above. `values="weather"` or `values="snow"` returns only one of the blocks.
- `measurements_snow` gives the snow depth and the new snow, each from its own station: of the 3 with
  the smallest distance plus 20 times the height gap, the first with a current value. Each has its own
  data_from, which names the station, its distance and height, unless the station is next to the place.
  - The snow depth comes from one of about 75 MeteoSwiss stations, one of about 130 automatic SLF
    stations or one of about 100 SLF manual measuring sites, where an observer measures the snow by hand each
    morning from November to April. A depth measured every 10 or 30 minutes counts for 210 minutes, as
    on [SLF's map](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/measured-values/information-about-snow-measurement/);
    an observer's counts until the next morning.
  - The new snow comes only from SLF: measured by an observer, or, at an automatic station, calculated by
    SLF from the snow depth, as old snow settles while new snow falls. Both cover the 24 hours up to
    about 06:00 UTC. The 3 days are the sum of three 24-hour values, as SLF adds them for its maps.
  - SLF says the observers' values are very accurate, but that most manual sites lie below about
    2000 m, so with moist snow or rain more new snow lies higher up.
  - Only stations within 25 km by distance plus 20 times the height gap are used, such as 15 km away and
    500 m higher, as snow changes with height and terrain. Tested on the 5817 forecast points: 94 % of
    those between 1200 and 2000 m keep a new snow station; below 800 m, where the nearest SLF station is
    often 40 km away and 600 m higher, 88 % get none, but 96 % still get a snow depth.
  - A block is null when none of its stations has a current value, and the tool says so when both are.
- `measurements_extremes` lists the first 5 stations by temperature or gust. For the wettest, it lists
  the first 5 rainy stations and counts the rainy stations in Switzerland and in each canton.
- For the sunniest it groups the stations by their sun in the last 10 minutes with the sunshine terms of
  [MeteoSwiss](https://www.meteoswiss.admin.ch/weather/weather-and-climate-from-a-to-z/sunshine.html):
  mostly sunny (8 to 10 minutes, 80 to 100 %), rather sunny (5 to 7), partly sunny (2 to 4), only a little
  sunshine (1) and hardly any sunshine (0). MeteoSwiss's ranges meet at 20, 50 and 80 %; a station
  exactly there counts for the sunnier term, so 2 minutes (20 %) is partly sunny. The cantons come in
  the order of their stations' average sun.

## Avalanche Bulletin
- `avalanche_bulletin` gives the [SLF avalanche bulletin](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/)
  valid now. In winter SLF publishes it every day at 17:00, valid until 17:00 the next day, with an update
  at 08:00 when needed; in summer and autumn only after heavy snowfall. When none is valid, the tool says so.
- SLF groups its warning regions with the same danger into areas, and each area is one bulletin with an
  outline. The tool takes the bulletin whose outline the place lies in. The bulletin covers the Alps and
  the Jura, never the Plateau, so for a place such as Zürich the tool says it lies in no region.
- The danger level goes from 1 low to 5 very high, from level 2 with a
  [subdivision](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/danger-levels/):
  `-` near the bottom, `=` in the middle and `+` near the top of the level, such as `considerable (3-)`. In
  spring a second level, `danger_level_later`, is the danger of the wet snow later in the day.
- Each avalanche problem, such as wind slab, has its own danger level, heights and aspects, the directions
  the slopes face. The answer gives the place's altitude to compare, but keeps every problem, as people go
  up from a village.
- The problems have SLF's summary and description. The snowpack, outlook, weather forecast and weather
  review are SLF's texts for all of Switzerland; the weather texts are missing on some days.
- The bulletins, about 80 KB, are downloaded again when the cached copy is older than 10 minutes.
- Every answer names SLF as its source, with a link, as SLF asks, and links SLF's map of the bulletin.

## Forecast Values Are Medians
- Temperature and rain are the median of the many possible outcomes MeteoSwiss calculates, not the
  most likely value.
- Medians do not add up: when showers are possible but unlikely in any 3-hour block, every block shows
  0 mm while the day's median is above 0.
- `forecast_daily` gives the rain of a day, and `forecast_rain` the chance and how much a wet hour may
  bring.

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
