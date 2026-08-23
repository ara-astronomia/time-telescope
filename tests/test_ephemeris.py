"""ephemeris.py: sun/moon times and darkness classification for a night,
via astral. Coordinates come from OBSERVATORY_LAT/OBSERVATORY_LON — the
test fixtures below use Rome's, matching the numbers astral itself was
verified against while designing this module (see docs/piano-35-effemeridi.md).
"""

from datetime import date, datetime, timedelta

import pytest

from ephemeris import classify_darkness, moon_info, sun_times

NIGHT = date(2026, 8, 17)


def close(actual, expected_hm, delta_minutes=2):
    expected = datetime.combine(actual.date(), datetime.min.time()).replace(
        hour=expected_hm[0], minute=expected_hm[1]
    )
    return abs((actual - expected).total_seconds()) <= delta_minutes * 60


class TestSunTimes:
    def test_sunset_and_dusk_are_on_the_nights_own_date(self, observatory_coordinates):
        times = sun_times(NIGHT)
        assert times["sunset"].date() == NIGHT
        assert times["dusk"].date() == NIGHT
        assert close(times["sunset"], (20, 7))
        assert close(times["dusk"], (21, 52))

    def test_dawn_and_sunrise_are_on_the_following_date(self, observatory_coordinates):
        times = sun_times(NIGHT)
        following = NIGHT + timedelta(days=1)
        assert times["dawn"].date() == following
        assert times["sunrise"].date() == following
        assert close(times["dawn"], (4, 36))
        assert close(times["sunrise"], (6, 21))

    def test_dusk_is_later_than_sunset(self, observatory_coordinates):
        times = sun_times(NIGHT)
        assert times["dusk"] > times["sunset"]

    def test_dawn_is_earlier_than_sunrise(self, observatory_coordinates):
        times = sun_times(NIGHT)
        assert times["dawn"] < times["sunrise"]

    def test_winter_night_is_longer_than_summer_night(self, observatory_coordinates):
        summer = sun_times(date(2026, 8, 17))
        winter = sun_times(date(2026, 12, 21))
        summer_darkness = summer["dawn"] - summer["dusk"]
        winter_darkness = winter["dawn"] - winter["dusk"]
        assert winter_darkness > summer_darkness


class TestMoonInfo:
    def test_phase_and_rise_set_match_verified_example(self, observatory_coordinates):
        info = moon_info(NIGHT)
        assert 4 < info["phase"] < 5
        assert close(info["moonrise"], (11, 37))
        assert close(info["moonset"], (22, 9))

    def test_culmination_matches_verified_example(self, observatory_coordinates):
        info = moon_info(NIGHT)
        assert close(info["culmination_time"], (15, 0), delta_minutes=10)
        assert 33 < info["culmination_altitude"] < 35

    def test_moonset_after_midnight_falls_back_to_the_following_date(self, observatory_coordinates):
        """2026-08-21: astral reports no moonset that calendar date (it
        actually happens just after midnight, on the 22nd) — verified by
        scanning a month of real dates while designing this module."""
        info = moon_info(date(2026, 8, 21))
        assert info["moonset"].date() == date(2026, 8, 22)
        assert close(info["moonset"], (0, 24))

    def test_moonrise_and_moonset_are_none_when_astral_raises_on_both_dates(
        self, observatory_coordinates, monkeypatch
    ):
        import astral.moon

        def never(observer, day, tzinfo):
            raise ValueError("Moon never sets on this date, at this location")

        monkeypatch.setattr(astral.moon, "moonset", never)
        info = moon_info(NIGHT)
        assert info["moonset"] is None


class TestClassifyDarkness:
    def test_slot_entirely_inside_full_darkness(self, observatory_coordinates):
        times = sun_times(NIGHT)
        result = classify_darkness(
            times["dusk"] + timedelta(minutes=10),
            times["dawn"] - timedelta(minutes=10),
            NIGHT,
        )
        assert result["darkness"] == "full"
        assert result["non_dark_intervals"] == []

    def test_slot_starting_before_dusk_is_partial(self, observatory_coordinates):
        times = sun_times(NIGHT)
        start = times["dusk"] - timedelta(hours=1)
        end = times["dawn"] - timedelta(hours=1)
        result = classify_darkness(start, end, NIGHT)
        assert result["darkness"] == "partial"
        assert result["non_dark_intervals"] == [{"start": start, "end": times["dusk"]}]

    def test_slot_ending_after_dawn_is_partial(self, observatory_coordinates):
        times = sun_times(NIGHT)
        start = times["dusk"] + timedelta(hours=1)
        end = times["dawn"] + timedelta(hours=1)
        result = classify_darkness(start, end, NIGHT)
        assert result["darkness"] == "partial"
        assert result["non_dark_intervals"] == [{"start": times["dawn"], "end": end}]

    def test_slot_entirely_in_daylight_is_none(self, observatory_coordinates):
        start = datetime.combine(NIGHT, datetime.min.time()).replace(hour=15)
        end = start + timedelta(hours=1)
        result = classify_darkness(start, end, NIGHT)
        assert result["darkness"] == "none"
        assert result["non_dark_intervals"] == [{"start": start, "end": end}]
