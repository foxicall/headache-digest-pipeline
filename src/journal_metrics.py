"""Verified, year-specific two-year JIF policy. Unknown journals fail closed."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

METRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "journal_metrics.json"
MINIMUM_JIF = Decimal("15")


def normalize_journal(source: str) -> str:
    # Ignore only our own society tags. Match complete known titles/aliases;
    # Nature Medicine must never inherit Nature's metrics by prefix matching.
    return " ".join(re.sub(r"[^a-z0-9]+", " ", source.split("|", 1)[0].casefold()).split())


class JournalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    journal: str = Field(min_length=1)
    aliases: list[str]
    jif: Decimal | None = Field(ge=0, allow_inf_nan=False)
    metric_year: int = Field(ge=2000)
    source_url: HttpUrl


class JournalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["Journal Impact Factor"]
    metric_year: int = Field(ge=2000)
    jcr_release_year: int
    threshold_exclusive: Literal[15]
    verified_on: date
    journals: list[JournalMetric]

    @model_validator(mode="after")
    def validate_registry(self):
        if self.jcr_release_year != self.metric_year + 1:
            raise ValueError("JCR release year must follow the JIF data year")
        if self.verified_on.year < self.jcr_release_year:
            raise ValueError("Verification cannot precede the JCR release year")
        owners = {}
        for index, entry in enumerate(self.journals):
            for name in [entry.journal, *entry.aliases]:
                key = normalize_journal(name)
                if not key or "|" in name or owners.get(key, index) != index:
                    raise ValueError("Journal aliases must be nonempty and unambiguous")
                owners[key] = index
        return self

    def lookup(self, source: str) -> JournalMetric | None:
        key = normalize_journal(source)
        return next((entry for entry in self.journals
                     if any(normalize_journal(name) == key for name in [entry.journal, *entry.aliases])), None)

    def eligible(self, source: str) -> bool:
        entry = self.lookup(source)
        return bool(entry and entry.metric_year == self.metric_year
                    and entry.jif is not None and entry.jif > MINIMUM_JIF)

    def pubmed_clause(self) -> str:
        names = list(dict.fromkeys(name for entry in self.journals if self.eligible(entry.journal)
                                   for name in [entry.journal, *entry.aliases]))
        # Do not turn an empty allowlist into an unrestricted query.
        return "(" + " OR ".join(f'"{name}"[Journal]' for name in names) + ")" if names else "0[PMID]"


def load_policy(path: Path = METRICS_PATH) -> JournalPolicy:
    return JournalPolicy.model_validate_json(path.read_text(encoding="utf-8"))
