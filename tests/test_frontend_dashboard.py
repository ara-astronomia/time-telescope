"""The dashboard shows the time a request came in: it must be the local
time of whoever is looking, not UTC passed off as local (#7)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import as_member

PAGE = "/telescope_time_dashboard.html"
TZ = ZoneInfo("Europe/Rome")
UTC = ZoneInfo("UTC")


@pytest.fixture
def browser_context_args(browser_context_args):
    """Fixed timezone: the test must not depend on the machine's."""
    return {**browser_context_args, "timezone_id": "Europe/Rome"}


def create_request(page, app_url, name, days_ahead):
    """Creates a request and returns (request, instants).

    There are two instants — before and after creation — because the
    minute can tick over between them: without that, the test would fail
    once in a while even though nothing is broken.
    """
    start = datetime.combine(date.today() + timedelta(days=days_ahead), time(22))
    before = datetime.now(TZ)
    research_program = page.request.post(
        f"{app_url}/telescope-time/research-programs", data={"name": name}
    ).json()
    request = page.request.post(
        f"{app_url}/telescope-time/requests",
        data={"research_program_id": research_program["id"], "observer": "Anna Verdi",
              "start": start.isoformat(),
              "end": (start + timedelta(hours=3)).isoformat()},
    ).json()
    return request, (before, datetime.now(TZ))


def create_request_with_slot(page, app_url, name, day, hour, duration):
    start = datetime.combine(day, time(hour))
    research_program = page.request.post(
        f"{app_url}/telescope-time/research-programs", data={"name": name}
    ).json()
    return page.request.post(
        f"{app_url}/telescope-time/requests",
        data={"research_program_id": research_program["id"], "start": start.isoformat(),
              "end": (start + timedelta(hours=duration)).isoformat()},
    ).json()


def meta_row(page, app_url, request_id):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(".richiesta-card")
    return page.locator(f"#card-{request_id} .rc-meta").inner_text()


def test_creation_time_shown_in_local_time(page, app_url):
    request, instants = create_request(page, app_url, "Orario", 20)
    expected = {m.strftime("%H:%M") for m in instants}

    row = meta_row(page, app_url, request["id"])

    assert any(t in row for t in expected), (
        f"expected one of {sorted(expected)} (Rome time), row: {row!r}"
    )


def test_utc_offset_does_not_appear(page, app_url):
    """Counter-check: the UTC time must not appear in place of the local
    one. The Italian offset is two hours in summer, one in winter."""
    request, instants = create_request(page, app_url, "Scarto", 21)
    utc_times = {m.astimezone(UTC).strftime("%H:%M") for m in instants}
    local_times = {m.strftime("%H:%M") for m in instants}
    if utc_times & local_times:
        pytest.skip("no offset between UTC and local time right now")

    row = meta_row(page, app_url, request["id"])

    for t in utc_times:
        assert t not in row, (
            f"showed UTC time {t} instead of one of {sorted(local_times)}"
        )


# ─── A slot conflict must be reported, not hidden (#33) ───────────────────────

def test_slot_conflict_is_shown_to_the_user(page, app_url):
    """The 409 names the conflicting request: if the dashboard flattens it
    to 'Errore durante l'aggiornamento', the reviewer doesn't know what to
    move."""
    day = date.today() + timedelta(days=25)
    approved = create_request_with_slot(page, app_url, "Conflitto A", day, hour=21, duration=3)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{approved['id']}", data={"status": "approved"}
    )
    second = create_request_with_slot(page, app_url, "Conflitto B", day, hour=23, duration=2)

    page.on("dialog", lambda d: d.accept())
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(f"#card-{second['id']}")
    page.click(f"#card-{second['id']} .rc-header")
    page.click(f"#card-{second['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    text = page.inner_text("#toast")
    assert f"#{approved['id']}" in text, f"the message doesn't name the conflict: {text!r}"


def test_the_conflicting_request_stays_pending(page, app_url):
    day = date.today() + timedelta(days=26)
    approved = create_request_with_slot(page, app_url, "Conflitto C", day, hour=21, duration=3)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{approved['id']}", data={"status": "approved"}
    )
    second = create_request_with_slot(page, app_url, "Conflitto D", day, hour=23, duration=2)

    page.on("dialog", lambda d: d.accept())
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(f"#card-{second['id']}")
    page.click(f"#card-{second['id']} .rc-header")
    page.click(f"#card-{second['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    after = page.request.get(f"{app_url}/telescope-time/requests/{second['id']}").json()
    assert after["status"] == "pending"


# ─── Rescheduling date and times from the dashboard (#34) ─────────────────────

def times_of(page, app_url, request_id):
    r = page.request.get(f"{app_url}/telescope-time/requests/{request_id}").json()
    return r["start"], r["end"]


def open_card(page, app_url, request_id):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector(f"#card-{request_id}")
    page.click(f"#card-{request_id} .rc-header")


def fill_reschedule(page, request_id, day, hour=23, duration=4, reason=""):
    start = datetime.combine(day, time(hour))
    page.fill(f"#sposta-inizio-{request_id}", start.strftime("%Y-%m-%dT%H:%M"))
    page.fill(f"#sposta-fine-{request_id}",
              (start + timedelta(hours=duration)).strftime("%Y-%m-%dT%H:%M"))
    if reason:
        page.fill(f"#sposta-motivo-{request_id}", reason)


def test_the_reschedule_command_is_also_on_approved_requests(page, app_url):
    """An approved request is a commitment made, not an immutable one: the
    weather changes and the night needs to move, not be canceled."""
    day = date.today() + timedelta(days=40)
    request = create_request_with_slot(page, app_url, "Sposta A", day, hour=21, duration=2)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}", data={"status": "approved"}
    )

    open_card(page, app_url, request["id"])

    assert page.locator(f"#sposta-inizio-{request['id']}").count() == 1
    assert page.locator(f"#card-{request['id']} .btn-sposta").count() == 1


def test_rejected_requests_can_also_be_rescheduled(page, app_url):
    """A request rejected for weather is recovered by rescheduling and
    re-approving it. If it couldn't be rescheduled first, it would have to
    be re-approved on its original slot — which may in the meantime be
    taken by another approved request, leaving no way out."""
    day = date.today() + timedelta(days=41)
    request = create_request_with_slot(page, app_url, "Sposta B", day, hour=21, duration=2)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}", data={"status": "rejected"}
    )

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], day + timedelta(days=1))
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    start, _ = times_of(page, app_url, request["id"])
    assert start == f"{day + timedelta(days=1)}T23:00:00"


def test_rescheduling_changes_the_times(page, app_url):
    day = date.today() + timedelta(days=42)
    request = create_request_with_slot(page, app_url, "Sposta C", day, hour=21, duration=2)

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], day + timedelta(days=1), reason="Manutenzione")
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    start, _ = times_of(page, app_url, request["id"])
    assert start == f"{day + timedelta(days=1)}T23:00:00"


def test_a_past_date_is_stated_before_confirming(page, app_url):
    """Rescheduling into the past is allowed, but it must be a conscious
    choice: the confirmation says so, instead of letting it through
    silently."""
    day = date.today() + timedelta(days=43)
    request = create_request_with_slot(page, app_url, "Sposta D", day, hour=21, duration=2)

    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], date.today() - timedelta(days=5))
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_timeout(400)

    assert messages, "no confirmation requested"
    assert "trascors" in messages[0].lower(), messages[0]


def test_declining_the_confirmation_reschedules_nothing(page, app_url):
    day = date.today() + timedelta(days=44)
    request = create_request_with_slot(page, app_url, "Sposta E", day, hour=21, duration=2)
    before = times_of(page, app_url, request["id"])

    page.on("dialog", lambda d: d.dismiss())
    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], day + timedelta(days=1))
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_timeout(400)

    assert times_of(page, app_url, request["id"]) == before


def test_end_past_the_night_is_flagged_and_not_sent(page, app_url):
    """`max` on #sposta-fine follows #sposta-inizio; with no `<form>` in
    this area, the check before the fetch is explicit."""
    day = date.today() + timedelta(days=46)
    request = create_request_with_slot(page, app_url, "Sposta H", day, hour=21, duration=2)
    before = times_of(page, app_url, request["id"])

    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], day + timedelta(days=2), hour=22, duration=27)
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    assert "notte" in page.inner_text("#toast")
    assert times_of(page, app_url, request["id"]) == before


def test_slot_conflict_blocks_the_reschedule(page, app_url):
    day = date.today() + timedelta(days=45)
    occupied = create_request_with_slot(page, app_url, "Sposta F", day, hour=21, duration=3)
    movable = create_request_with_slot(page, app_url, "Sposta G", day + timedelta(days=2), hour=21, duration=2)
    for r in (occupied, movable):
        page.request.patch(
            f"{app_url}/telescope-time/requests/{r['id']}", data={"status": "approved"}
        )

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, movable["id"])
    fill_reschedule(page, movable["id"], day, hour=22, duration=2)
    page.click(f"#card-{movable['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    text = page.inner_text("#toast")
    assert f"#{occupied['id']}" in text, text


# ─── Changing a decision already made (#45) ────────────────────────────────────

def decided(page, app_url, name, days_ahead, status, hour=21):
    day = date.today() + timedelta(days=days_ahead)
    request = create_request_with_slot(page, app_url, name, day, hour=hour, duration=2)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}", data={"status": status}
    )
    return request


def status_of(page, app_url, request_id):
    return page.request.get(
        f"{app_url}/telescope-time/requests/{request_id}"
    ).json()["status"]


def test_an_approved_request_can_be_rejected(page, app_url):
    request = decided(page, app_url, "Ribalta A", 50, "approved")

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    assert status_of(page, app_url, request["id"]) == "rejected"


def test_a_rejected_request_can_be_approved(page, app_url):
    request = decided(page, app_url, "Ribalta B", 51, "rejected")

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.click(f"#card-{request['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    assert status_of(page, app_url, request["id"]) == "approved"


def test_the_command_that_changes_nothing_is_not_shown(page, app_url):
    """The server already treats re-approving an approved request as a
    non-event: showing the button would suggest an action that does
    nothing."""
    approved = decided(page, app_url, "Ribalta C", 52, "approved")
    rejected = decided(page, app_url, "Ribalta D", 53, "rejected")

    open_card(page, app_url, approved["id"])
    assert page.locator(f"#card-{approved['id']} .btn-approve").count() == 0
    assert page.locator(f"#card-{approved['id']} .btn-reject").count() == 1

    open_card(page, app_url, rejected["id"])
    assert page.locator(f"#card-{rejected['id']} .btn-reject").count() == 0
    assert page.locator(f"#card-{rejected['id']} .btn-approve").count() == 1


def test_a_pending_request_has_both_commands(page, app_url):
    day = date.today() + timedelta(days=54)
    request = create_request_with_slot(page, app_url, "Ribalta E", day, hour=21, duration=2)

    open_card(page, app_url, request["id"])

    assert page.locator(f"#card-{request['id']} .btn-approve").count() == 1
    assert page.locator(f"#card-{request['id']} .btn-reject").count() == 1


def test_reversing_warns_the_outcome_was_already_sent(page, app_url):
    """This isn't the same as a first decision: the observer has already
    received one outcome and will receive a second."""
    request = decided(page, app_url, "Ribalta F", 55, "approved")

    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    open_card(page, app_url, request["id"])
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_timeout(400)

    assert messages, "no confirmation requested"
    text = messages[0].lower()
    assert "email" in text, messages[0]
    assert "approvazione" in text or "già" in text, messages[0]


def test_notes_remain_editable_on_a_decided_request(page, app_url):
    request = decided(page, app_url, "Ribalta G", 56, "approved")

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.fill(f"#note-{request['id']}", "Previsioni peggiorate")
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    notes = page.request.get(
        f"{app_url}/telescope-time/requests/{request['id']}"
    ).json()["reviewer_notes"]
    assert notes == "Previsioni peggiorate"


# ─── The history becomes visible ───────────────────────────────────────────────

def test_the_history_is_shown_in_the_detail(page, app_url):
    """Without it, a reversal would be indistinguishable from a decision
    made only once — which is exactly what justifies allowing it."""
    request = decided(page, app_url, "Storico A", 57, "approved")
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}",
        data={"status": "rejected", "reviewer_notes": "Meteo peggiorato"},
    )

    open_card(page, app_url, request["id"])
    page.wait_for_selector(f"#storico-{request['id']} .voce")

    text = page.inner_text(f"#storico-{request['id']}")
    assert "approved" in text.lower()
    assert "rejected" in text.lower()
    assert "Meteo peggiorato" in text


def test_the_history_also_shows_reschedules(page, app_url):
    day = date.today() + timedelta(days=58)
    request = create_request_with_slot(page, app_url, "Storico B", day, hour=21, duration=2)
    new_start = datetime.combine(day + timedelta(days=1), time(23))
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}/schedule",
        data={"start": new_start.isoformat(),
              "end": (new_start + timedelta(hours=3)).isoformat(),
              "reason": "Manutenzione"},
    )

    open_card(page, app_url, request["id"])
    page.wait_for_selector(f"#storico-{request['id']} .voce")

    text = page.inner_text(f"#storico-{request['id']}")
    assert "23:00" in text, text
    assert "Manutenzione" in text


def test_a_never_decided_request_declares_an_empty_history(page, app_url):
    day = date.today() + timedelta(days=59)
    request = create_request_with_slot(page, app_url, "Storico C", day, hour=21, duration=2)

    open_card(page, app_url, request["id"])
    page.wait_for_selector(f"#storico-{request['id']}")

    assert page.locator(f"#storico-{request['id']} .voce").count() == 0
    assert page.inner_text(f"#storico-{request['id']}").strip() != ""


def test_clearing_the_notes_deletes_them(page, app_url):
    """The field opens pre-filled: if clearing it deleted nothing, it
    would look editable without actually being so."""
    request = decided(page, app_url, "Note A", 60, "approved")
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}",
        data={"status": "approved", "reviewer_notes": "Da cancellare"},
    )

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.fill(f"#note-{request['id']}", "")
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    notes = page.request.get(
        f"{app_url}/telescope-time/requests/{request['id']}"
    ).json()["reviewer_notes"]
    assert not notes, f"the notes weren't cleared: {notes!r}"


def test_existing_notes_appear_in_the_field(page, app_url):
    request = decided(page, app_url, "Note B", 61, "approved")
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}",
        data={"status": "approved", "reviewer_notes": "Meteo stabile"},
    )

    open_card(page, app_url, request["id"])

    assert page.input_value(f"#note-{request['id']}") == "Meteo stabile"


# ─── The detail panel doesn't collapse out from under you ─────────────────────

def is_open(page, request_id):
    return "open" in (page.locator(f"#detail-{request_id}").get_attribute("class") or "")


def test_the_card_stays_open_after_a_decision(page, app_url):
    """Rebuilding the list used to close the card being worked on: with two
    or three actions in a row on the same request, it now reopens every
    time."""
    request = decided(page, app_url, "Aperta A", 62, "approved")

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert is_open(page, request["id"])


def test_the_history_updates_without_reopening(page, app_url):
    request = decided(page, app_url, "Aperta B", 63, "approved")

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    page.click(f"#card-{request['id']} .btn-reject")
    page.wait_for_selector("#toast.show")
    page.wait_for_function(
        f"document.querySelectorAll('#storico-{request['id']} .voce').length === 2"
    )

    assert "rejected" in page.inner_text(f"#storico-{request['id']}")


def test_the_card_stays_open_after_a_reschedule(page, app_url):
    day = date.today() + timedelta(days=64)
    request = create_request_with_slot(page, app_url, "Aperta C", day, hour=21, duration=2)

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, request["id"])
    fill_reschedule(page, request["id"], day + timedelta(days=1))
    page.click(f"#card-{request['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert is_open(page, request["id"])


def test_closed_cards_stay_closed(page, app_url):
    day = date.today() + timedelta(days=65)
    open_one = create_request_with_slot(page, app_url, "Aperta D", day, hour=21, duration=2)
    closed_one = create_request_with_slot(page, app_url, "Aperta E", day, hour=23, duration=2)

    page.on("dialog", lambda d: d.accept())
    open_card(page, app_url, open_one["id"])
    page.click(f"#card-{open_one['id']} .btn-approve")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert not is_open(page, closed_one["id"])


# ─── Identity: hiding reviewer commands from non-reviewers (#26) ──────────────

def test_a_member_does_not_see_reviewer_commands(page, app_url):
    day = date.today() + timedelta(days=70)
    request = create_request_with_slot(page, app_url, "Nascosti", day, hour=21, duration=2)

    as_member(page)
    open_card(page, app_url, request["id"])

    assert page.locator(f"#detail-{request['id']} .action-area").count() == 0
    assert page.locator(f"#detail-{request['id']} .sposta-area").count() == 0


def test_the_banner_shows_who_is_logged_in(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Marta Conti" in page.inner_text("#utente-corrente")


def test_the_banner_follows_the_user_switch(page, app_url):
    as_member(page)
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "mario" in page.inner_text("#utente-corrente")


def test_403_on_approval_reached_via_an_already_hidden_button(page, app_url):
    """Someone who had the page open before a group change no longer sees
    the buttons, but the function is still callable: the message must be
    distinct from a generic network error (#26)."""
    day = date.today() + timedelta(days=71)
    request = create_request_with_slot(page, app_url, "403 stato", day, hour=21, duration=2)

    as_member(page)
    open_card(page, app_url, request["id"])
    page.on("dialog", lambda d: d.accept())
    # `aggiornaStato` reads #note-N from the DOM, which doesn't exist here
    # because the area is hidden: it's injected to reproduce the "page open
    # from before the group change" scenario without depending on the
    # hidden markup.
    page.evaluate(f"""() => {{
        const i = document.createElement('textarea');
        i.id = 'note-{request["id"]}';
        document.body.appendChild(i);
    }}""")
    page.evaluate(f"aggiornaStato({request['id']}, 'approved', 'pending')")
    page.wait_for_selector("#toast.show")

    assert page.inner_text("#toast") == 'Solo i responsabili possono approvare o rifiutare.'

    after = page.request.get(f"{app_url}/telescope-time/requests/{request['id']}").json()
    assert after["status"] == "pending"


def test_403_on_reschedule_reached_via_an_already_hidden_button(page, app_url):
    day = date.today() + timedelta(days=72)
    request = create_request_with_slot(page, app_url, "403 orario", day, hour=21, duration=2)

    as_member(page)
    open_card(page, app_url, request["id"])
    page.on("dialog", lambda d: d.accept())
    # `spostaOrario` reads the #sposta-inizio-N/#sposta-fine-N fields from
    # the DOM, which don't exist here because the area is hidden: they are
    # injected before calling the function, to reproduce the "page open
    # from before the group change" scenario without depending on the
    # hidden markup.
    new_start = datetime.combine(day + timedelta(days=1), time(22)).strftime("%Y-%m-%dT%H:%M")
    new_end   = datetime.combine(day + timedelta(days=1), time(23)).strftime("%Y-%m-%dT%H:%M")
    page.evaluate(f"""() => {{
        const mk = (id, val) => {{ const i = document.createElement('input'); i.id = id; i.value = val; document.body.appendChild(i); }};
        mk('sposta-inizio-{request["id"]}', '{new_start}');
        mk('sposta-fine-{request["id"]}', '{new_end}');
        mk('sposta-motivo-{request["id"]}', '');
    }}""")
    page.evaluate(f"spostaOrario({request['id']})")
    page.wait_for_selector("#toast.show")

    assert page.inner_text("#toast") == 'Solo i responsabili possono spostare una richiesta.'


# ─── Dev role switcher (#26) ────────────────────────────────────────────────────

def test_the_dev_switcher_is_visible(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")


def test_the_active_role_is_highlighted(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#dev-switcher")
    assert "active" in (page.get_attribute("#dev-btn-responsabile", "class") or "")
    assert "active" not in (page.get_attribute("#dev-btn-socio", "class") or "")

    with page.expect_navigation():
        page.click("#dev-btn-socio")
    page.wait_for_selector("#dev-switcher")
    assert "active" in (page.get_attribute("#dev-btn-socio", "class") or "")
    assert "active" not in (page.get_attribute("#dev-btn-responsabile", "class") or "")


def test_switching_to_member_hides_the_commands_without_restarting(page, app_url):
    day = date.today() + timedelta(days=73)
    request = create_request_with_slot(page, app_url, "Switcher", day, hour=21, duration=2)

    page.goto(f"{app_url}{PAGE}")
    page.wait_for_selector("#dev-switcher")
    with page.expect_navigation():
        page.click("#dev-btn-socio")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Luca Bertani" in page.inner_text("#utente-corrente")

    page.click(f"#card-{request['id']} .rc-header")
    page.wait_for_timeout(200)
    assert page.locator(f"#detail-{request['id']} .action-area").count() == 0


def test_link_to_the_calendar_is_present(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    assert page.locator('a[href="telescope_time_calendario.html"]').count() == 1


def test_link_to_the_request_form_is_present(page, app_url):
    page.goto(f"{app_url}{PAGE}")
    assert page.locator('a[href="telescope_time_request.html"]').count() == 1


# ─── The observer reschedules their own pending request ───────────────────────

def create_own_request(page, app_url, name, day, hour=21, duration=2, headers=None):
    research_program = page.request.post(
        f"{app_url}/telescope-time/research-programs", data={"name": name}, headers=headers or {}
    ).json()
    start = datetime.combine(day, time(hour))
    return page.request.post(
        f"{app_url}/telescope-time/requests",
        data={"research_program_id": research_program["id"], "start": start.isoformat(),
              "end": (start + timedelta(hours=duration)).isoformat()},
        headers=headers or {},
    ).json()


def test_the_owner_sees_the_reschedule_on_their_own_pending_request(page, app_url):
    as_member(page)
    day = date.today() + timedelta(days=74)
    request = create_own_request(page, app_url, "Propria A", day)

    open_card(page, app_url, request["id"])

    assert page.locator(f"#detail-{request['id']} .sposta-area").count() == 1
    assert page.locator(f"#detail-{request['id']} .action-area").count() == 0


def test_the_owner_does_not_see_the_reschedule_on_their_own_approved_request(page, app_url):
    as_member(page)
    day = date.today() + timedelta(days=75)
    request = create_own_request(page, app_url, "Propria B", day)
    page.request.patch(
        f"{app_url}/telescope-time/requests/{request['id']}",
        data={"status": "approved"},
        headers={"Remote-User": "anna", "Remote-Groups": "soci,telescope-responsabili"},
    )

    open_card(page, app_url, request["id"])

    assert page.locator(f"#detail-{request['id']} .sposta-area").count() == 0
