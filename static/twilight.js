// Shared sun/twilight fragments — one source of truth for
// request.html, calendar.html and dashboard.html, which all show the same
// data (a night's sunset/dusk/dawn/sunrise, as returned by
// GET /telescope-time/ephemeris) in slightly different places.
//
// The markup itself lives in twilight-block.html / twilight-compact.html,
// not as HTML built from JS template strings: each is fetched once, cached,
// and cloned into every container that needs it; this file only fills in
// the times.

const twilightFragmentCache = {};

function fetchTwilightFragment(path) {
  if (!twilightFragmentCache[path]) {
    twilightFragmentCache[path] = fetch(path).then(r => r.text());
  }
  return twilightFragmentCache[path];
}

/** `instant` is an ISO string in observatory local time (never UTC to
 * shift, unlike a `Date` built from it) — read directly, not parsed. */
function formatClockTime(instant) {
  return instant.slice(11, 16);
}

function fillTwilightFields(container, sun) {
  container.querySelectorAll('[data-tw]').forEach(el => {
    el.textContent = formatClockTime(sun[el.dataset.tw]);
  });
}

/** The three-line fragment (twilight-block.html) — request.html's hint,
 * calendar.html's day detail panel, dashboard.html's expanded request. */
async function renderSunTimesBlock(container, sun) {
  container.innerHTML = await fetchTwilightFragment('twilight-block.html');
  fillTwilightFields(container, sun);
}

/** The compact single-line fragment (twilight-compact.html), for
 * calendar.html's day cells. */
async function renderDayTwilight(container, sun) {
  container.innerHTML = await fetchTwilightFragment('twilight-compact.html');
  fillTwilightFields(container, sun);
}
