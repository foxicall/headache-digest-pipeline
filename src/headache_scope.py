"""Keyless topic screening for the user-supplied ICHD-3 beta scope.

The catalog describes retrieval scope, never a diagnosis assigned to a paper.
Underlying diseases alone are insufficient for secondary-headache admission.
"""

from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data/headache_syndromes.json"


class SyndromeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(pattern=r"^A?(?:[1-9]|1[0-4])(?:\.\d+)*$")
    name: str = Field(min_length=1, pattern=r"[A-Za-z]")
    context: Literal["syndrome", "migraine", "headache", "ocular_pain", "craniofacial_pain"]


class SyndromeGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(pattern=r"^A?(?:[1-9]|1[0-4])$")
    label: str = Field(min_length=1)


class SyndromeCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edition: Literal["ICHD-3 beta (2013), user-supplied scope"]
    source_url: HttpUrl
    current_classification_url: HttpUrl
    groups: list[SyndromeGroup] = Field(min_length=1)
    entries: list[SyndromeEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_codes_and_groups(self):
        groups = {group.code for group in self.groups}
        codes = {entry.code for entry in self.entries}
        if len(groups) != len(self.groups) or len(codes) != len(self.entries):
            raise ValueError("Syndrome/group codes must be unique")
        if not groups <= codes or any(entry.code.split(".")[0] not in groups for entry in self.entries):
            raise ValueError("Every syndrome must belong to a catalog group with a root entry")
        return self


def load_catalog(path: Path = CATALOG_PATH) -> SyndromeCatalog:
    return SyndromeCatalog.model_validate_json(path.read_text(encoding="utf-8"))


CATALOG = load_catalog()

# Wildcards retrieve spelling/plural variants; no standalone stroke, infection,
# tumor, MS, CADASIL, MELAS or generic trigeminal anatomy branch is admitted.
SEARCH_TERMS = (
    "migraine", "migrain*", "headache*", "cephalalgi*", "cephalgi*", "hemicrani*",
    "head pain*", "head and neck pain*", "head and/or neck pain*", "facial pain*", "craniofacial pain*", "orofacial pain*",
    "cranial neuralgia", "trigeminal neuralgia", "trigeminal neuropath*",
    "trigeminal postherpetic neuralgia", "trigeminal post herpetic neuralgia",
    "glossopharyngeal neuralgia", "nervus intermedius", "geniculate neuralgia",
    "occipital neuralgia", "painful ophthalmoplegic neuropathy",
    "Tolosa Hunt", "paratrigeminal oculosympathetic", "Raeder*",
    "burning mouth", "glossodynia", "stomatodynia",
    "SUNCT", "SUNA", "HaNDL", "NDPH", "epicrania fugax",
    "persistent aura without infarction", "typical aura without headache",
    "cyclic vomiting", "cyclical vomiting", "benign paroxysmal vertigo",
    "benign paroxysmal torticollis", "infantile colic", "alternating hemiplegia of childhood",
)


def pubmed_term(term: str) -> str:
    return f'"{term}"[Title/Abstract]' if " " in term else f"{term}[Title/Abstract]"


HEADACHE_QUERY = "(" + " OR ".join(pubmed_term(term) for term in SEARCH_TERMS) + (
    ' OR (("optic neuritis"[Title/Abstract] OR "cranial neuropath*"[Title/Abstract]) AND pain*[Title/Abstract])'
    ' OR (("central neuropathic pain"[Title/Abstract] OR "central post stroke pain"[Title/Abstract])'
    ' AND (facial[Title/Abstract] OR face[Title/Abstract] OR craniofacial[Title/Abstract]'
    ' OR craniocervical[Title/Abstract] OR orofacial[Title/Abstract] OR trigeminal[Title/Abstract])))'
)

TOPIC = re.compile(
    r"\b(?:migrain\w*|head[-\s]*aches?|cephalalgi\w*|cephalgi\w*|hemicrani\w*|"
    r"(?:head|facial|craniofacial|orofacial)[-\s]+pains?|"
    r"trigeminal[-\s]+neuralgia|SUNCT|SUNA|HaNDL|NDPH)\b", re.I)


def phrase_pattern(name: str) -> str:
    # Treat hyphens/dashes/punctuation consistently, with whole-word boundaries.
    return r"\b" + r"[\W_]+".join(re.escape(word) for word in re.findall(r"\w+", name)) + r"\b"


# Every direct syndrome name in the supplied catalog is usable, including names
# without "headache" such as epicrania fugax and migraine-associated syndromes.
DIRECT_NAMES = [name for entry in CATALOG.entries if entry.context == "syndrome"
                for name in (entry.name, re.sub(r"\s*\([^)]*\)", "", entry.name).strip())]
ALIASES = (
    "cyclic vomiting syndrome", "geniculate neuralgia", "Raeder syndrome",
    "Raeder's syndrome", "paratrigeminal oculosympathetic syndrome",
    "glossodynia", "stomatodynia", "trigeminal neuropathic pain",
    "trigeminal postherpetic neuralgia", "trigeminal post herpetic neuralgia",
    "postherpetic trigeminal neuropathy", "painful optic neuritis",
    "cranial neuralgia", "painful cranial neuropathy", "nervus intermedius neuralgia",
)
NAMED_SYNDROME = re.compile("|".join(phrase_pattern(name) for name in dict.fromkeys([*DIRECT_NAMES, *ALIASES])), re.I)
OPTIC_NEURITIS = re.compile(r"\boptic[-\s]+neuritis\b", re.I)
PAIN_FOCUS = re.compile(r"\bpain(?:ful)?\b|\banalges\w*\b", re.I)
CENTRAL_PAIN = re.compile(r"\bcentral (?:neuropathic|post[-\s]stroke) pain\b", re.I)
CRANIAL_REGION = re.compile(r"\b(?:facial|face|craniofacial|craniocervical|orofacial|trigeminal)\b", re.I)


def has_topic(text: str) -> bool:
    return bool(TOPIC.search(text) or NAMED_SYNDROME.search(text)
                or (OPTIC_NEURITIS.search(text) and PAIN_FOCUS.search(text))
                or (CENTRAL_PAIN.search(text) and CRANIAL_REGION.search(text)))


INCIDENTAL = re.compile(r"(?:symptoms?|side effects?|adverse (?:events?|effects?|reactions?))\s+"
                        r"(?:\w+\s+){0,3}(?:included?|including|such as|were|are|:)", re.I)
GOAL = re.compile(r"\b(?:we (?:aim(?:ed)?|sought)|(?:this|the) study (?:aim(?:s|ed)?|sought)|"
                  r"(?:our |the )?(?:objectives?|aims?|purposes?) (?:of [^.!?]{0,100}? )?(?:was|were|is|are)|"
                  r"we (?:investigated|evaluated|examined|assessed|compared|studied))\b", re.I)
PRIMARY_OUTCOME = re.compile(r"\bprimary (?:outcomes?|endpoints?)\b", re.I)
MIGRAINE_THERAPY = re.compile(r"\b(?:erenumab|fremanezumab|galcanezumab|eptinezumab|"
                              r"rimegepant|ubrogepant|atogepant|zavegepant|lasmiditan|"
                              r"anti[- ]CGRP|CGRP[- ](?:monoclonal antibodies|mAbs))\b", re.I)
MIGRAINE_CONTEXT = re.compile(r"(?:treatment|prevention) of (?:migraine|cluster headache)|"
                              r"migraine (?:prevention|treatment)|"
                              r"patients? with (?:(?:episodic|chronic) )?migraine", re.I)


def is_headache_topic(title: str, abstract: str) -> bool:
    """Conservative, keyless topic screen; journal and affiliations are not evidence.

    Accept a title topic, an explicit study objective/primary outcome, or a
    sustained unstructured focus. Named migraine therapies need migraine context;
    migraine-associated episodic syndromes and painful cranial/facial syndromes
    are also in scope.
    Generic trigeminal anatomy and isolated symptom/adverse-event mentions fail.
    This transparent lexical screen favors precision over exhaustive retrieval.
    """
    # Clinical-reasoning titles often enumerate symptoms of an unrelated disease.
    symptom_list_title = bool(re.search(r"\b(?:man|woman|boy|girl|patient|child) with\b", title, re.I)
                              and ("," in title or re.search(r"\band\b", title, re.I)))
    if has_topic(title) and not INCIDENTAL.search(title) and not symptom_list_title:
        return True
    therapy = MIGRAINE_THERAPY.search(title)
    if therapy and MIGRAINE_CONTEXT.search(abstract):
        return True
    sections = abstract.splitlines()
    structured = any(re.match(r"^[A-Z][A-Z /&-]+:", section) for section in sections)
    for section in sections:
        label, separator, body = section.partition(":")
        if separator and re.fullmatch(r"(?:OBJECTIVES?|AIMS?|PURPOSE)", label, re.I):
            if any(has_topic(sentence) and not INCIDENTAL.search(sentence)
                   for sentence in re.split(r"(?<=[.!?])\s+", body)):
                return True
        for sentence in re.split(r"(?<=[.!?])\s+", section):
            goal = GOAL.search(sentence) or PRIMARY_OUTCOME.search(sentence)
            if goal and has_topic(sentence[goal.start():]) and not INCIDENTAL.search(sentence):
                return True
    if not structured:
        sentences = re.split(r"(?<=[.!?])\s+", abstract)
        focused = [sentence for sentence in sentences if has_topic(sentence) and not INCIDENTAL.search(sentence)]
        # A single symptom in a case report is insufficient. The opening topic
        # must be developed again, e.g. migraine aura and its pathophysiology.
        return bool(sentences and has_topic(sentences[0])
                    and not INCIDENTAL.search(sentences[0]) and len(focused) >= 2)
    return False

