"""Sun/moon times and darkness classification for an observing night, via
astral. All returned instants are naive, observatory-local — the same
convention the rest of the API uses for times crossing its boundary."""

from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import astral.moon
from astral import Depression, LocationInfo
from astral.sun import sun

from config import observatory_lat, observatory_lon, observatory_tz

CULMINATION_SAMPLE_STEP = timedelta(minutes=5)


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


def _moon_event(fn: Callable, night: date) -> Optional[datetime]:
    """astral reports a moonrise/moonset either as `None` (the event falls
    on the following calendar date, not this one) or by raising `ValueError`
    ("Moon never sets/rises on this date, at this location") — not one
    consistent convention. Both are retried on the following date, since a
    night runs past midnight into it; if that fails too, the event genuinely
    doesn't happen during this night."""
    observer = _observer()
    tz = observatory_tz()
    for day in (night, night + timedelta(days=1)):
        try:
            result = fn(observer, day, tzinfo=tz)
        except ValueError:
            continue
        if result is not None:
            return result.replace(tzinfo=None)
    return None


def _moon_culmination(night: date) -> tuple[Optional[datetime], Optional[float]]:
    """Highest point the moon reaches, sampled across `night`'s full
    calendar date (not just the dark hours: the culmination the issue's
    own verified example reports happens mid-afternoon)."""
    observer = _observer()
    tz = observatory_tz()
    start = datetime.combine(night, datetime.min.time(), tzinfo=ZoneInfo(tz))
    steps = int(timedelta(days=1) / CULMINATION_SAMPLE_STEP)

    best_time, best_altitude = None, None
    for i in range(steps):
        moment = start + CULMINATION_SAMPLE_STEP * i
        altitude = astral.moon.elevation(observer, moment)
        if best_altitude is None or altitude > best_altitude:
            best_time, best_altitude = moment, altitude

    if best_time is None:
        return None, None
    return best_time.replace(tzinfo=None), round(best_altitude, 1)


def moon_info(night: date) -> dict:
    culmination_time, culmination_altitude = _moon_culmination(night)
    return {
        "phase": round(astral.moon.phase(night), 1),
        "moonrise": _moon_event(astral.moon.moonrise, night),
        "moonset": _moon_event(astral.moon.moonset, night),
        "culmination_time": culmination_time,
        "culmination_altitude": culmination_altitude,
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
