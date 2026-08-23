"""Outbound email notifications — synchronous, called inline in the
handler (see send_message)."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import now_at_observatory, to_local

SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "crac@osservatorio.it")
REVIEWER_EMAIL = os.environ.get("REVIEWER_EMAIL", "responsabile@osservatorio.it")


def send_message(recipient: str, subject: str, body: str):
    """Single sending point. Without SMTP configured, it just logs.

    Isolating it here makes it possible to verify *to whom* a message is
    sent without a mail server, and it's the spot to touch when sending
    moves to BackgroundTasks (#8).
    """
    if not SMTP_HOST or not SMTP_USER:
        print(f"[SMTP non configurato] a {recipient}: {subject}", flush=True)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipient, msg.as_string())
    except Exception as e:
        print(f"[Errore invio email] a {recipient}: {e}", flush=True)


def readable_time_slot(request: dict) -> str:
    """'12/09/2026 22:00 → 13/09/2026 01:00', without repeating the date
    when the session doesn't cross midnight. `start`/`end` are UTC on the
    row; this is the observatory-local rendering a human reads."""
    start = to_local(request["start"])
    end = to_local(request["end"])
    end_format = "%H:%M" if start.date() == end.date() else "%d/%m/%Y %H:%M"
    return f"{start:%d/%m/%Y %H:%M} → {end:{end_format}}"


def send_notification_email(request: dict, research_program: dict):
    body = f"""
Nuova richiesta tempo telescopio ricevuta.

Osservatore:      {request['observer']}
Co-osservatori:   {request['co_observers'] or '—'}
Ricerca:          {research_program['name']}
Fascia oraria:    {readable_time_slot(request)}

Descrizione ricerca:
{research_program['description'] or '—'}

Specifiche:
{research_program['specs'] or '—'}

Accedi alla dashboard CRaC per approvare o rifiutare la richiesta.
    """.strip()

    send_message(
        REVIEWER_EMAIL,
        f"[CRaC] Nuova richiesta tempo telescopio — {research_program['name']}",
        body,
    )


def send_outcome_email(request: dict):
    """The address comes from the registry, which takes it from Authelia.
    If it's missing, the notice goes to the reviewer, who at least knows
    they need to relay it in person."""
    outcome = "✅ APPROVATA" if request["status"] == "approved" else "❌ RIFIUTATA"
    body = f"""
La tua richiesta di tempo telescopio è stata: {outcome}

Osservatore:       {request['observer']}
Ricerca:           {request['research_program_name']}
Fascia oraria:     {readable_time_slot(request)}
Note responsabile: {request['reviewer_notes'] or '—'}
    """.strip()

    send_message(
        request["observer_email"] or REVIEWER_EMAIL,
        f"[CRaC] Richiesta {outcome} — {request['research_program_name']}",
        body,
    )


def send_reschedule_email(request: dict, previous: dict, reason: Optional[str]):
    """The observer got assigned a different time than requested: not
    something they should stumble on by chance opening the calendar."""
    warning = ""
    if to_local(request["start"]) < now_at_observatory():
        warning = "\n\nAttenzione: la nuova fascia cade in una data passata."

    body = f"""
La tua osservazione è stata riprogrammata dal responsabile.

Osservatore:  {request['observer']}
Ricerca:      {request['research_program_name']}
Prima:        {readable_time_slot(previous)}
Adesso:       {readable_time_slot(request)}
Motivo:       {reason or '—'}{warning}
    """.strip()

    send_message(
        request["observer_email"] or REVIEWER_EMAIL,
        f"[CRaC] Osservazione riprogrammata — {request['research_program_name']}",
        body,
    )
