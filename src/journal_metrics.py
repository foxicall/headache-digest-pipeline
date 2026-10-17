"""Verified, year-specific two-year JIF policy. Unknown journals fail closed."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

METRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "journal_metrics.json"
MINIMUM_JIF = Decimal("10")
# NLM Publication Types, not title/abstract keywords or AI-inferred designs.
# https://www.nlm.nih.gov/mesh/pubtypes.html
CLINICAL_TRIAL_TYPES = frozenset({
    "Clinical Trial", "Clinical Trial, Phase I", "Clinical Trial, Phase II",
    "Clinical Trial, Phase III", "Clinical Trial, Phase IV",
    "Controlled Clinical Trial", "Randomized Controlled Trial",
    "Pragmatic Clinical Trial", "Adaptive Clinical Trial", "Equivalence Trial",
})
NON_TRIAL_TYPES = frozenset({
    "Clinical Trial Protocol", "Clinical Trial, Veterinary", "Observational Study",
    "Review", "Systematic Review", "Meta-Analysis",
})


def is_clinical_trial(study_design: str) -> bool:
    types = {" ".join(value.casefold().split()) for value in study_design.split(";")}
    return bool(types & {value.casefold() for value in CLINICAL_TRIAL_TYPES}) and not bool(
        types & {value.casefold() for value in NON_TRIAL_TYPES})


def clinical_trial_clause() -> str:
    trials = " OR ".join(f'"{name}"[Publication Type]' for name in sorted(CLINICAL_TRIAL_TYPES))
    exclusions = " OR ".join(f'"{name}"[Publication Type]' for name in sorted(NON_TRIAL_TYPES))
    return f"(({trials}) NOT ({exclusions}))"


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
    threshold_exclusive: Literal[10]
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

    def eligible(self, source: str, study_design: str = "") -> bool:
        entry = self.lookup(source)
        return bool(entry and entry.metric_year == self.metric_year
                    and entry.jif is not None and (entry.jif > MINIMUM_JIF
                    or (entry.jif < MINIMUM_JIF and is_clinical_trial(study_design))))

    def pubmed_clause(self) -> str:
        def journal_clause(entries):
            names = dict.fromkeys(name for entry in entries for name in [entry.journal, *entry.aliases])
            return "(" + " OR ".join(f'"{name}"[Journal]' for name in names) + ")"

        verified = [entry for entry in self.journals
                    if entry.metric_year == self.metric_year and entry.jif is not None]
        high = [entry for entry in verified if entry.jif > MINIMUM_JIF]
        low = [entry for entry in verified if entry.jif < MINIMUM_JIF]
        branches = [journal_clause(high)] if high else []
        if low:
            branches.append(f"({journal_clause(low)} AND {clinical_trial_clause()})")
        # Do not turn an empty allowlist into an unrestricted query.
        return "(" + " OR ".join(branches) + ")" if branches else "0[PMID]"


def load_policy(path: Path = METRICS_PATH) -> JournalPolicy:
    return JournalPolicy.model_validate_json(path.read_text(encoding="utf-8"))
