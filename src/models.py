"""Shared metadata, seven-field extraction, and repository record contracts."""

from datetime import date
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArticleMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pmid_or_id: str = Field(min_length=1)
    doi: str
    title: str = Field(min_length=1)
    source_society_or_journal: str = Field(min_length=1)
    publication_date: str
    study_design: str = Field(min_length=1)

    @field_validator("publication_date")
    @classmethod
    def iso_publication_date(cls, value: str) -> str:
        if value != "Not reported":
            if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
                raise ValueError("Expected ISO year, year-month or full date")
            date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
        return value


class StructuredSummary(BaseModel):
    """Exactly seven required fields, including three nonempty string lists."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    methods: str = Field(description="Reported study methods, design, intervention, comparator and duration.")
    inclusion_criteria: list[str] = Field(description="Only explicitly reported inclusion criteria; otherwise ['Not reported'] or ['Not applicable'] with a reason.")
    exclusion_criteria: list[str] = Field(description="Only explicitly reported exclusion criteria; never infer exclusions from participant characteristics.")
    number_of_inclusion: str = Field(description="Reported participant counts, distinguishing randomized/enrolled/analyzed. Use Not reported or Not applicable; never calculate or infer a number.")
    mechanism_of_action_moa: str = Field(description="Only the mechanism explicitly described in the source; otherwise Not reported or Not applicable.")
    conclusion: str = Field(description="Source conclusions and reported outcomes, preserving effect sizes, uncertainty and harms without inventing statistics.")
    pitfalls_and_limitations: list[str] = Field(description="Reported limitations and clearly labeled limitations of abstract-only evidence; distinguish these from author-reported limitations.")

    @field_validator("*")
    @classmethod
    def nonempty_content(cls, value):
        # Keep the wire schema compatible with Structured Outputs' JSON subset.
        if isinstance(value, list):
            value = [item.strip() for item in value]
            if not value or any(not item for item in value):
                raise ValueError("Use an explicit missing-information marker, not an empty list/item")
        elif not value:
            raise ValueError("Use an explicit missing-information marker, not an empty string")
        return value


class Article(BaseModel):
    """Crawler input to the summarizer; source text is separate from metadata."""

    model_config = ConfigDict(extra="forbid")
    metadata: ArticleMetadata
    url: str
    abstract: str = ""
    affiliations: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def source_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Expected an HTTP(S) source URL without credentials")
        return value


class PaperRecord(BaseModel):
    """The public JSON contract: metadata plus exactly seven clinical fields."""

    model_config = ConfigDict(extra="forbid")
    article_metadata: ArticleMetadata
    structured_summary: StructuredSummary
