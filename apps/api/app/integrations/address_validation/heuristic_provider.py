"""The default address-validation provider -- a deterministic, rule-based
scorer with NO external API call and NO credentials required, so the
feature works out of the box. Used whenever no external provider is
configured (`AddressValidationConfig.from_settings()` returns `None` --
see `get_address_validation_provider` in this package's `config.py`) and
as the graceful-degradation target if a configured external provider
fails (see `AddressValidationService`).

Every score is the *documented* result of the rules below, computed the
same way for the same input every time -- never `random()`, never a
placeholder number. `reason` always lists exactly which rule(s) fired,
so a fulfillment user (or a debugging engineer) can see precisely why an
address scored the way it did, not just the number.

Deliberately conservative: this is pattern-matching on structure and
known placeholder/junk text, not a real postal-database lookup (no
external API was available to verify one against), so it CANNOT confirm
an address genuinely exists or is deliverable -- it can only catch
addresses that are structurally incomplete or obviously fake. See this
module's own limitations, restated in the final report.
"""

from __future__ import annotations

import re

from app.integrations.address_validation.provider import (
    AddressValidationProvider,
    AddressValidationResult,
)
from app.models.enums import AddressValidationStatus

# Case-insensitive substrings that show up in placeholder/test/junk
# addresses far more often than in real ones. Deliberately short and
# specific (never a bare "test" against a full free-text field without
# word-boundary anchoring) to avoid false positives against genuine
# street names that happen to contain a common word.
_JUNK_KEYWORDS = (
    "n/a",
    "not applicable",
    "dummy",
    "sample address",
    "test address",
    "asdf",
    "xxxx",
    "....",
)

_REPEATED_CHAR_RE = re.compile(r"(.)\1{4,}")  # e.g. "aaaaa", "11111"
_ONLY_DIGITS_RE = re.compile(r"^\d+$")
_INDIA_PIN_RE = re.compile(r"^\d{6}$")

# score >= VALID_THRESHOLD -> VALID; >= AMBIGUOUS_THRESHOLD -> AMBIGUOUS;
# below that -> JUNK. Chosen to match the worked examples in the
# feature request (95% valid, 50% ambiguous, 24% junk).
VALID_THRESHOLD = 80
AMBIGUOUS_THRESHOLD = 40


def _score_address(address: dict) -> tuple[int, list[str]]:
    """Pure function, no I/O -- returns (score 0-100, reason fragments).
    Kept separate from the class below so it's trivially unit-testable
    without constructing a provider instance.
    """
    score = 100
    issues: list[str] = []

    line1 = (address.get("line1") or "").strip()
    city = (address.get("city") or "").strip()
    state = (address.get("state") or "").strip()
    pin_code = (address.get("pin_code") or "").strip()
    country = (address.get("country") or "").strip()

    if not line1 or line1 == "—":
        score -= 40
        issues.append("Missing street address")
    elif len(line1) < 8:
        score -= 15
        issues.append("Street address is very short")

    if not city or city == "—":
        score -= 25
        issues.append("Missing city")

    if not state:
        score -= 10
        issues.append("Missing state")

    if not pin_code:
        score -= 20
        issues.append("Missing PIN code")
    elif country.lower() in ("india", "in", ""):
        if not _INDIA_PIN_RE.match(pin_code):
            score -= 20
            issues.append("PIN code is not a valid 6-digit Indian PIN")
    elif not (3 <= len(pin_code) <= 10):
        score -= 10
        issues.append("PIN/postal code length looks invalid")

    haystack = f"{line1} {city}".lower()
    for keyword in _JUNK_KEYWORDS:
        if keyword in haystack:
            score -= 40
            issues.append(f"Contains placeholder text ('{keyword}')")
            break  # one match is enough signal; avoid double-counting

    if _REPEATED_CHAR_RE.search(line1):
        score -= 30
        issues.append("Street address contains repeated/spam-like characters")

    if line1 and _ONLY_DIGITS_RE.match(line1) and len(line1) > 4:
        score -= 15
        issues.append("Street address is only digits, no street name")

    return max(0, min(100, score)), issues


def _bucket(score: int) -> AddressValidationStatus:
    if score >= VALID_THRESHOLD:
        return AddressValidationStatus.VALID
    if score >= AMBIGUOUS_THRESHOLD:
        return AddressValidationStatus.AMBIGUOUS
    return AddressValidationStatus.JUNK


class HeuristicAddressValidationProvider(AddressValidationProvider):
    """No network call, no credentials, always available. Batch is just
    a loop over the pure `_score_address` function above -- there is no
    external round trip to actually batch here, but the interface is
    still honored so callers never need to special-case "which provider
    is active."
    """

    async def validate_batch(self, addresses: list[dict]) -> list[AddressValidationResult]:
        results = []
        for address in addresses:
            score, issues = _score_address(address)
            reason = "; ".join(issues) if issues else "No issues detected."
            results.append(
                AddressValidationResult(status=_bucket(score), score=score, reason=reason)
            )
        return results
