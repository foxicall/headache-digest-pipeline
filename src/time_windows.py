"""Calendar windows shared by retrieval and archive validation."""

from datetime import date, datetime, timezone

DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_TRIAL_LOOKBACK_YEARS = 10


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def years_before(today: date, years: int) -> date:
    """Calendar anniversary; February 29 clamps to February 28."""
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def trial_date_eligible(publication_date: str, today: date, years=DEFAULT_TRIAL_LOOKBACK_YEARS) -> bool:
    # PubMed may match a later issue date than the electronic date we display.
    # Use the displayed source date as an additional conservative age check.
    try:
        published = date.fromisoformat(publication_date + {4: "-01-01", 7: "-01", 10: ""}[len(publication_date)])
    except (KeyError, ValueError):
        return False
    return years_before(today, years) <= published <= today
