"""Two simultaneous HTTP requests: the dashboard always does this (Promise.all
on /requests and /research-programs), so it isn't a lab-only scenario."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx2 as httpx


def test_simultaneous_calls_do_not_fail(app_url):
    def call(path):
        return httpx.get(f"{app_url}{path}", timeout=10).status_code

    paths = ["/telescope-time/requests", "/telescope-time/research-programs"] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(call, paths))

    assert set(outcomes) == {200}, f"unexpected outcomes: {sorted(set(outcomes))}"


# ─── The overlap constraint holds under concurrency (#33, #34) ────────────────

def test_two_simultaneous_approvals_do_not_create_an_overlap(app_url, monkeypatch):
    """There's a window between the conflict check and the write: without an
    exclusive transaction, two simultaneous approvals both cross it and the
    overlap the constraint exists to prevent gets created anyway.

    The injected delay makes the window always observable, instead of
    leaving the outcome to chance.
    """
    import time
    import router

    original = router.already_approved_at_same_time

    def slow(*args, **kwargs):
        outcome = original(*args, **kwargs)
        time.sleep(0.3)
        return outcome

    monkeypatch.setattr(router, "already_approved_at_same_time", slow)

    night = (date.today() + timedelta(days=70)).isoformat()
    ids = []
    for hour in ("21:00:00", "22:00:00"):
        research_program = httpx.post(f"{app_url}/telescope-time/research-programs",
                             json={"name": f"Gara {night} {hour}"}).json()
        ids.append(httpx.post(f"{app_url}/telescope-time/requests", json={
            "research_program_id": research_program["id"],
            "start": f"{night}T{hour}",
            "end": f"{night}T23:59:00",
        }).json()["id"])

    def approve(request_id):
        return httpx.patch(f"{app_url}/telescope-time/requests/{request_id}",
                           json={"status": "approved"}, timeout=30).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, ids))

    approved = [
        httpx.get(f"{app_url}/telescope-time/requests/{i}").json()["status"] for i in ids
    ].count("approved")
    assert approved == 1, f"two overlapping slots were both approved (outcomes {outcomes})"
