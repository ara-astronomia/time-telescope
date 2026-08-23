"""Sun times and darkness classification for an observing night, via
astral. All returned instants are naive, observatory-local — the same
convention the rest of the API uses for times crossing its boundary."""

from datetime import date, datetime, timedelta

from astral import Depression, LocationInfo
from astral.sun import sun

from config import observatory_lat, observatory_lon, observatory_tz


def _observer():
    return LocationInfo(
        timezone=observatory_tz(),
        latitude=observatory_lat(),
        longitude=observatory_lon(),
    ).observer


def sun_times(night: date) -> dict:
    """Sunset and the start of full darkness (astronomical dusk, sun at
    -18°) belong to `night`'s own date; the end of full darkness and
    sunrise belong to the following date — a night spans midnight, so one
    `sun()` call per date is needed."""
    observer = _observer()
    tz = observatory_tz()
    evening = sun(observer, date=night, dawn_dusk_depression=Depression.ASTRONOMICAL, tzinfo=tz)
    morning = sun(
        observer, date=night + timedelta(days=1), dawn_dusk_depression=Depression.ASTRONOMICAL, tzinfo=tz
    )
    return {
        "sunset": evening["sunset"].replace(tzinfo=None),
        "dusk": evening["dusk"].replace(tzinfo=None),
        "dawn": morning["dawn"].replace(tzinfo=None),
        "sunrise": morning["sunrise"].replace(tzinfo=None),
    }


def classify_darkness(start: datetime, end: datetime, night: date) -> dict:
    """Classifies a time slot against the night's full-darkness window
    (dusk to dawn): `full` entirely inside it, `none` entirely outside,
    `partial` straddling an edge. `non_dark_intervals` names the portions
    outside darkness, for a UI to show explicitly."""
    times = sun_times(night)
    dark_start, dark_end = times["dusk"], times["dawn"]

    non_dark_intervals = []
    if start < dark_start:
        non_dark_intervals.append({"start": start, "end": min(end, dark_start)})
    if end > dark_end:
        non_dark_intervals.append({"start": max(start, dark_end), "end": end})

    if not non_dark_intervals:
        darkness = "full"
    elif non_dark_intervals == [{"start": start, "end": end}]:
        darkness = "none"
    else:
        darkness = "partial"

    return {"darkness": darkness, "non_dark_intervals": non_dark_intervals}
