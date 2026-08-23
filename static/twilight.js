// Shared rendering for sun/twilight info (#35) — one source of truth for
// request.html, calendar.html and dashboard.html, which all show the same
// data (a night's sunset/dusk/dawn/sunrise, as returned by
// GET /telescope-time/ephemeris) in slightly different places.

/** `instant` is an ISO string in observatory local time (never UTC to
 * shift, unlike a `Date` built from it) — read directly, not parsed. */
function formatClockTime(instant) {
  return instant.slice(11, 16);
}

/** The three-line block used in request.html's hint, calendar.html's day
 * detail panel, and dashboard.html's expanded request. */
function renderSunTimesBlock(sun) {
  return `🌇 Tramonto: ore ${formatClockTime(sun.sunset)}<br>` +
         `🌌 Crepuscolo astronomico: tra le ${formatClockTime(sun.dusk)} e le ${formatClockTime(sun.dawn)}<br>` +
         `🌅 Alba: ore ${formatClockTime(sun.sunrise)}`;
}

/** The compact single-line version for calendar.html's day cells. */
function renderDayTwilight(sun) {
  return `🌌 ${formatClockTime(sun.dusk)}–${formatClockTime(sun.dawn)}`;
}
