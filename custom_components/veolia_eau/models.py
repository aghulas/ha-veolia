"""Shapes of the Veolia consumption rows (pure functions, no network, no Home Assistant)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# fiabilite_index / fiabilite_conso
RELIABILITY = {"M": "measured", "E": "estimated", "C": "corrected", "R": "read"}


@dataclass(frozen=True)
class DailyReading:
    """One day of consumption, dated on the day it was consumed."""

    day: date
    litres: float | None
    index_m3: float | None
    reliability: str | None


@dataclass(frozen=True)
class MonthlyReading:
    """One month of consumption."""

    year: int
    month: int
    m3: float | None
    index_m3: float | None
    reliability: str | None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field(row: dict[str, Any], key: str, unit: str) -> float | None:
    inner = row.get(key)
    return _number(inner.get(unit)) if isinstance(inner, dict) else None


def parse_daily(rows: Iterable[dict[str, Any]]) -> list[DailyReading]:
    """Daily rows → readings sorted by day, one per day (the last row of a day wins).

    Rows without a valid `date_releve` are dropped.
    """
    by_day: dict[date, DailyReading] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date_releve"))[:10])
        except ValueError:
            continue
        code = row.get("fiabilite_index")
        by_day[day] = DailyReading(
            day=day,
            litres=_field(row, "consommation", "litre"),
            index_m3=_field(row, "index", "m3"),
            reliability=RELIABILITY.get(code, code) if isinstance(code, str) else None,
        )
    return [by_day[day] for day in sorted(by_day)]


def parse_monthly(rows: Iterable[dict[str, Any]]) -> list[MonthlyReading]:
    """Monthly rows → readings sorted by (year, month), one per month."""
    by_month: dict[tuple[int, int], MonthlyReading] = {}
    for row in rows:
        year, month = row.get("annee"), row.get("mois")
        if not isinstance(year, int) or not isinstance(month, int) or not 1 <= month <= 12:
            continue
        code = row.get("fiabilite_conso")
        by_month[(year, month)] = MonthlyReading(
            year=year,
            month=month,
            m3=_field(row, "consommation", "m3"),
            index_m3=_field(row, "index", "m3"),
            reliability=RELIABILITY.get(code, code) if isinstance(code, str) else None,
        )
    return [by_month[key] for key in sorted(by_month)]


@dataclass(frozen=True)
class HourlyReading:
    """One hour of consumption. `start` is Veolia's local time, naive (no offset)."""

    start: datetime
    litres: float


def parse_hourly(rows: Iterable[dict[str, Any]]) -> list[HourlyReading]:
    """Hourly rows → readings sorted by start, one per hour (the last row of an hour wins).

    Rows without a valid `heure_debut_releve` or without a volume are dropped: an hour Veolia
    does not publish is unknown, not zero.
    """
    by_hour: dict[datetime, HourlyReading] = {}
    for row in rows:
        try:
            start = datetime.fromisoformat(str(row.get("heure_debut_releve")))
        except ValueError:
            continue
        litres = _field(row, "consommation", "litre")
        if litres is None:
            continue
        start = start.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        by_hour[start] = HourlyReading(start=start, litres=litres)
    return [by_hour[hour] for hour in sorted(by_hour)]


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """(year, month) pairs covering [start, end], in order; empty if end < start."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


@dataclass(frozen=True)
class Subscription:
    """One subscription of the account, as listed by /espace-client or /espace-client/pro."""

    id: str
    meter: str | None
    address: str | None

    @property
    def label(self) -> str:
        """Text shown to the account owner when choosing subscriptions."""
        parts = [self.address, f"n° {self.meter}" if self.meter else None]
        return " — ".join(p for p in parts if p) or self.id


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def parse_subscriptions(payload: Any) -> list[Subscription]:
    """contacts[].tiers[].abonnements[] → subscriptions, deduplicated, sorted by id.

    Works for both account descriptions: `id_abonnement` is an int in one and a string in the other.
    """
    found: dict[str, Subscription] = {}
    contacts = payload.get("contacts") if isinstance(payload, dict) else None
    for contact in _dicts(contacts):
        for tiers in _dicts(contact.get("tiers")):
            for row in _dicts(tiers.get("abonnements")):
                raw = row.get("id_abonnement")
                if isinstance(raw, bool) or not isinstance(raw, int | str):
                    continue
                sub_id = str(raw).strip()
                if not sub_id.isdigit() or sub_id in found:
                    continue
                found[sub_id] = Subscription(
                    id=sub_id,
                    meter=_text(row.get("numero_compteur")),
                    address=_text(row.get("adresse_de_branchement")),
                )
    return sorted(found.values(), key=lambda s: s.id)


@dataclass(frozen=True)
class SubscriptionInfo:
    """What we keep of /abonnements/<id>/facturation (the rest holds banking data)."""

    pds: str
    start: date
    reading_mode: str | None
    index_m3: float | None
    index_date: date | None
    balance: float | None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_subscription_info(data: dict[str, Any]) -> SubscriptionInfo | None:
    """None when the fields needed to query consumption are missing."""
    pds, start = _text(data.get("numero_pds")), _date(data.get("date_debut_abonnement"))
    if pds is None or start is None:
        return None
    return SubscriptionInfo(
        pds=pds,
        start=start,
        reading_mode=_text(data.get("mode_releve")),
        index_m3=_number(data.get("dernier_index_releve")),
        index_date=_date(data.get("date_index_releve")),
        balance=_number(data.get("solde")),
    )


@dataclass(frozen=True)
class Invoice:
    """An invoice (not a reminder, not a payment plan)."""

    day: date
    amount: float | None


INVOICE_CODE = "CFAEAU"


def last_invoice(rows: Iterable[dict[str, Any]]) -> Invoice | None:
    """Most recent invoice of the documents list."""
    invoices = [
        Invoice(day, _number(row.get("montant_facture_ttc")))
        for row in rows
        if row.get("code_document") == INVOICE_CODE
        and (day := _date(row.get("date_emission"))) is not None
    ]
    return max(invoices, key=lambda i: i.day, default=None)


@dataclass(frozen=True)
class BilledPeriod:
    """A reading used for an invoice."""

    start: date | None
    end: date
    m3: float | None
    index_m3: float | None


def last_billed_period(rows: Iterable[dict[str, Any]]) -> BilledPeriod | None:
    """Most recent billing reading."""
    periods = [
        BilledPeriod(
            start=_date(row.get("date_facturation_precedente")),
            end=end,
            m3=_field(row, "volume_facture_eau", "m3"),
            index_m3=_number(row.get("index_facture")),
        )
        for row in rows
        if (end := _date(row.get("date_facturation"))) is not None
    ]
    return max(periods, key=lambda p: p.end, default=None)


def monthly_as_readings(
    monthly: Iterable[MonthlyReading], since: date, before: date
) -> list[DailyReading]:
    """Monthly volumes as one reading on the 1st of each month, in litres.

    Only whole months from `since`'s month on and strictly before `before`'s month: they fill the
    history that precedes the daily data without overlapping it.
    """
    return [
        DailyReading(date(m.year, m.month, 1), m.m3 * 1000.0, m.index_m3, m.reliability)
        for m in monthly
        if m.m3 is not None
        and (since.year, since.month) <= (m.year, m.month) < (before.year, before.month)
    ]
