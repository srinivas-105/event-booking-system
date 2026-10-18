"""Plain-text + HTML bodies. Every dynamic value is HTML-escaped (organizers control event text)."""
from html import escape

from app.utils import fmt_dt, fmt_money, utcnow

SIGN_OFF = "Event Booking Team"


def _render(name: str, intro: str, rows: list[tuple[str, str]], outro: str) -> tuple[str, str]:
    text_rows = "\n".join(f"{label}: {value}" for label, value in rows)
    text = f"Hi {name},\n\n{intro}\n\n{text_rows}\n\n{outro}\n\nRegards,\n{SIGN_OFF}\n"
    html_rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#555'>{escape(label)}</td>"
        f"<td style='padding:2px 0'><strong>{escape(value)}</strong></td></tr>"
        for label, value in rows
    )
    html = (
        "<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.5'>"
        f"<p>Hi {escape(name)},</p><p>{escape(intro)}</p>"
        f"<table>{html_rows}</table><p>{escape(outro)}</p>"
        f"<p>Regards,<br>{SIGN_OFF}</p></div>"
    )
    return text, html


def booking_confirmation(customer_name: str, event, booking) -> tuple[str, str, str]:
    subject = f"Booking Confirmed — {event.title}"
    rows = [
        ("Event", event.title),
        ("Venue", event.venue),
        ("Date", fmt_dt(event.event_date)),
        ("Tickets booked", str(booking.quantity)),
        ("Price per ticket", fmt_money(booking.unit_price)),
        ("Total ticket price", fmt_money(booking.total_price)),
        ("Booking reference", booking.reference),
    ]
    text, html = _render(
        customer_name,
        "Your booking has been confirmed!",
        rows,
        "Thank you for booking. Please keep this confirmation for your records.",
    )
    return subject, text, html


def event_updated(customer_name: str, event_title: str, change_rows: list[tuple[str, str]],
                  description_changed: bool, price_changed: bool) -> tuple[str, str, str]:
    subject = f"Important Update — {event_title}"
    rows = [("Event", event_title), *change_rows]
    if description_changed:
        rows.append(("Description", "The event description was updated"))
    rows.append(("Updated at", fmt_dt(utcnow())))
    outro = "Your booking remains valid. Please review the updated details before attending."
    if price_changed:
        outro += " Your booking keeps the price you originally paid."
    text, html = _render(customer_name, "The organizer has updated an event you booked.", rows, outro)
    return subject, text, html
