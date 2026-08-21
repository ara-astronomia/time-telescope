"""Client-side validation of the request page.

The server remains the only guarantor of correctness (see test_time_slots.py):
here we verify that the user gets immediate feedback and that clearly wrong
requests don't even get sent.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PAGE = "/request.html"

# `datetime-local` doesn't accept seconds: the value is 'YYYY-MM-DDTHH:MM'.
FORMAT = "%Y-%m-%dT%H:%M"


def time_slot(days_ahead, hour=22, duration=3):
    start = datetime.combine(date.today() + timedelta(days=days_ahead), time(hour))
    return start.strftime(FORMAT), (start + timedelta(hours=duration)).strftime(FORMAT)


def prepare(page, app_url):
    """Opens the page with a research program already available in the menu,
    and #start's `min` already set — it's populated once GET /observatory
    resolves, asynchronously after the page loads."""
    page.request.post(
        f"{app_url}/telescope-time/research-programs",
        data={"name": f"Research {datetime.now()}"},
    )
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#research_program_id option", state="attached")
    page.wait_for_function("document.getElementById('start').min !== ''")
    return page


def fill_form(page, start, end):
    """The observer's name isn't filled in: it comes from Authelia (#5)."""
    page.select_option("#research_program_id", index=1)
    page.fill("#start", start)
    page.fill("#end", end)


def submitted_starts(page, app_url):
    return [r["start"] for r in page.request.get(f"{app_url}/telescope-time/requests").json()]


# ─── Time slot in the past ─────────────────────────────────────────────────────

def test_time_slot_in_the_past_is_not_submitted(page, app_url):
    prepare(page, app_url)
    start, end = time_slot(-30)
    fill_form(page, start, end)
    page.click("#btn-submit")
    page.wait_for_timeout(500)

    assert f"{start}:00" not in submitted_starts(page, app_url)


def test_time_slot_in_the_past_is_flagged_to_the_user(page, app_url):
    prepare(page, app_url)
    fill_form(page, *time_slot(-30))
    page.click("#btn-submit")

    assert page.locator("#start").evaluate("e => e.validity.rangeUnderflow") is True


# ─── End before start ──────────────────────────────────────────────────────────

def test_end_before_start_is_flagged_to_the_user(page, app_url):
    """The constraint is relative to the other field: `min` on #end follows #start."""
    prepare(page, app_url)
    start, end = time_slot(10)
    fill_form(page, end, start)          # swapped
    page.click("#btn-submit")

    assert page.locator("#end").evaluate("e => e.validity.rangeUnderflow") is True


def test_end_before_start_is_not_submitted(page, app_url):
    prepare(page, app_url)
    start, end = time_slot(10)
    fill_form(page, end, start)
    page.click("#btn-submit")
    page.wait_for_timeout(500)

    assert submitted_starts(page, app_url) == [] or f"{end}:00" not in submitted_starts(page, app_url)


# ─── End past the night of the start (#59) ─────────────────────────────────────

def test_end_past_the_night_is_flagged_to_the_user(page, app_url):
    """`max` on #end follows the night of #start."""
    prepare(page, app_url)
    start, end = time_slot(10, hour=22, duration=27)
    fill_form(page, start, end)
    page.click("#btn-submit")

    assert page.locator("#end").evaluate("e => e.validity.rangeOverflow") is True


def test_end_past_the_night_is_not_submitted(page, app_url):
    prepare(page, app_url)
    start, end = time_slot(10, hour=22, duration=27)
    fill_form(page, start, end)
    page.click("#btn-submit")
    page.wait_for_timeout(500)

    assert f"{start}:00" not in submitted_starts(page, app_url)


# ─── Required fields and submission ────────────────────────────────────────────

def test_required_fields_declared_in_markup(page, app_url):
    prepare(page, app_url)
    for field in ("#research_program_id", "#start", "#end"):
        assert page.locator(field).evaluate("e => e.required") is True, field


def test_future_time_slot_is_submitted(page, app_url):
    prepare(page, app_url)
    start, end = time_slot(11)
    fill_form(page, start, end)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    assert f"{start}:00" in submitted_starts(page, app_url)


def test_server_validation_error_shown_to_the_user(page, app_url):
    """If the client is bypassed, the server's 422 must not turn into a
    generic 'Errore durante l'invio'."""
    prepare(page, app_url)
    fill_form(page, *time_slot(12))
    # Bypass the browser's constraint like someone manipulating the DOM
    # would: an input[type=datetime-local] rejects a non-conforming value,
    # so its type has to be changed first.
    page.evaluate("""
        const field = document.getElementById('start');
        field.type = 'text';
        field.value = 'domani';
    """)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    toast_text = page.inner_text("#toast").lower()
    assert "orario" in toast_text or "fascia" in toast_text, f"the message doesn't name the field: {toast_text!r}"


# ─── Identity is no longer typed (#5) ──────────────────────────────────────────

def test_the_name_is_not_typed_anymore(page, app_url):
    prepare(page, app_url)
    assert page.locator("#observer").count() == 0, "the name field is still in the form"


def test_the_page_shows_who_you_are(page, app_url):
    prepare(page, app_url)
    page.wait_for_selector("#current-user")
    assert "Marta Conti" in page.inner_text("#current-user")


def test_the_request_is_submitted_without_a_name(page, app_url):
    prepare(page, app_url)
    start, end = time_slot(13)
    fill_form(page, start, end)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    submitted = page.request.get(f"{app_url}/telescope-time/requests").json()
    mine = [r for r in submitted if r["start"] == f"{start}:00"]
    assert mine, "the request wasn't recorded"
    assert all(r["observer"] == "Marta Conti" for r in mine)


# ─── Identity: role switcher in dev ──────────────────────────────────────

def test_the_dev_switcher_is_visible(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")


def test_links_to_calendar_and_dashboard_are_present(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    assert page.locator('a[href="calendar.html"]').count() == 1
    assert page.locator('a[href="dashboard.html"]').count() == 1


# ─── Observatory timezone ────────────────────────────────────────────────

def test_the_start_minimum_reflects_the_observatory_not_the_browser(browser, app_url):
    """#start's `min` and the copy above it must both come from the
    observatory's clock (Europe/Rome, default), regardless of the visiting
    browser's own timezone — here emulated as Pacific/Kiritimati, 13 hours
    ahead of Rome."""
    context = browser.new_context(timezone_id="Pacific/Kiritimati")
    page = context.new_page()
    try:
        prepare(page, app_url)
        shown_min = datetime.strptime(page.locator("#start").evaluate("e => e.min"), FORMAT)
        note = page.locator("#observatory-tz-note").inner_text()

        expected = datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
        assert abs((shown_min - expected).total_seconds()) < 120
        assert "Europe/Rome" in note
    finally:
        context.close()
