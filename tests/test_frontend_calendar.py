"""The calendar must make visible when multiple observations share the same
night (#4), and distinguish a night that's merely requested from one that's
truly contested (#33)."""

from datetime import date, datetime, time, timedelta

PAGE = "/telescope_time_calendario.html"


def next_month() -> date:
    """Requests are only accepted in the future: next month is entirely in
    the future no matter what today's day is."""
    today = date.today()
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def create_request(page, app_url, night, hour, duration=2, name=None, approved=False):
    start = datetime.combine(night, time(hour))
    research_program = page.request.post(
        f"{app_url}/telescope-time/research-programs",
        data={"name": name or f"Research program {night} {hour}"},
    ).json()
    request = page.request.post(
        f"{app_url}/telescope-time/requests",
        data={
            "research_program_id": research_program["id"],
            "start": start.isoformat(),
            "end": (start + timedelta(hours=duration)).isoformat(),
        },
    ).json()
    if approved:
        page.request.patch(
            f"{app_url}/telescope-time/requests/{request['id']}",
            data={"status": "approved"},
        )
    return request


def open_calendar(page, app_url, night):
    """Opens the calendar on the month of `night`, which isn't the current one."""
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(".day-cell")
    page.click(".month-nav button:last-of-type")
    page.wait_for_selector(f'.day-cell[data-giorno="{night.isoformat()}"]')
    return page.locator(f'.day-cell[data-giorno="{night.isoformat()}"]')


def css_class(cell):
    return cell.get_attribute("class") or ""


# ─── Multiple programs sharing a night (#4) ────────────────────────────────────

def test_night_with_two_programs_flagged_in_grid(page, app_url):
    night = next_month().replace(day=11)
    create_request(page, app_url, night, hour=18, approved=True)
    create_request(page, app_url, night, hour=21, approved=True)

    cell = open_calendar(page, app_url, night)
    assert cell.locator(".turni").count() == 1
    assert "2" in cell.locator(".turni").inner_text()


def test_night_with_a_single_program_not_flagged(page, app_url):
    night = next_month().replace(day=12)
    create_request(page, app_url, night, hour=18, approved=True)

    cell = open_calendar(page, app_url, night)
    assert cell.locator(".turni").count() == 0
    assert "booked" in css_class(cell)


# ─── Requested ≠ contested (#33, absorbs #42) ──────────────────────────────────

def test_a_single_request_does_not_color_the_night_as_contested(page, app_url):
    night = next_month().replace(day=13)
    create_request(page, app_url, night, hour=21)

    cell = open_calendar(page, app_url, night)
    assert "pending" in css_class(cell)
    assert "contested" not in css_class(cell)


def test_two_overlapping_requests_color_the_night_as_contested(page, app_url):
    night = next_month().replace(day=14)
    create_request(page, app_url, night, hour=21, duration=3)
    create_request(page, app_url, night, hour=22, duration=3)

    cell = open_calendar(page, app_url, night)
    assert "contested" in css_class(cell)


def test_two_requests_in_distinct_shifts_are_not_contested(page, app_url):
    night = next_month().replace(day=15)
    create_request(page, app_url, night, hour=18, duration=2)
    create_request(page, app_url, night, hour=21, duration=2)

    cell = open_calendar(page, app_url, night)
    assert "pending" in css_class(cell)
    assert "contested" not in css_class(cell)


def test_legend_distinguishes_the_two_states(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(".legenda")
    legend = page.inner_text(".legenda").lower()

    assert "richiesta" in legend
    assert "contesa" in legend


# ─── The time slot is visible in the detail panel ──────────────────────────────

def test_night_detail_shows_the_time_slot(page, app_url):
    night = next_month().replace(day=16)
    create_request(page, app_url, night, hour=21, duration=3)

    cell = open_calendar(page, app_url, night)
    cell.click()
    page.wait_for_selector("#overlay.open")

    assert "21:00" in page.inner_text("#dp-content")
    assert "00:00" in page.inner_text("#dp-content")


# ─── Identity: the calendar also shows who's logged in (#26) ──────────────────

def test_banner_shows_who_is_logged_in(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Marta Conti" in page.inner_text("#utente-corrente")


def test_the_dev_switcher_is_visible(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")


def test_links_to_dashboard_and_request_form_are_present(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    assert page.locator('a[href="telescope_time_request.html"]').count() == 1
    assert page.locator('a[href="telescope_time_dashboard.html"]').count() == 1
