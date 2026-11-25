"""Validated contracts and deterministic matching."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Annotated, Literal, Protocol
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Source = Literal["fixture", "manual", "alert"]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Watchlist(Contract):
    name: Short
    enabled: bool = True
    sources: list[Source] = Field(default_factory=lambda: ["fixture"], min_length=1, max_length=3)
    phrase: str = Field(default="", max_length=160)
    include: list[Short] = Field(default_factory=list, max_length=24)
    exclude: list[Short] = Field(default_factory=lambda: ["for parts", "broken", "empty box", "wanted"], max_length=24)
    max_price: Money
    min_price: Money | None = None
    currency: Currency = "CAD"
    location: Short | None = "Halifax"
    radius_km: float | None = Field(default=None, ge=0, le=20000)
    conditions: list[Short] = Field(default_factory=list, max_length=16)
    max_age_hours: int | None = Field(default=None, ge=1, le=87600)
    priority: int = Field(default=0, ge=0, le=10)
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    notify_new: bool = True
    notify_drops: bool = True
    drafts: bool = True
    review_uncertain: bool = True
    initial: Literal["silent", "digest", "alert"] = "silent"
    weights: tuple[int, int, int, int] = (45, 30, 15, 10)

    @model_validator(mode="after")
    def consistent(self):
        if self.min_price is not None and self.min_price > self.max_price:
            raise ValueError("Minimum price exceeds maximum")
        if self.radius_km is not None and not self.location:
            raise ValueError("Radius requires a location")
        if self.weights and (min(self.weights) < 0 or sum(self.weights) != 100):
            raise ValueError("Score weights must be nonnegative and sum to 100")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("Sources must be unique")
        return self


def canonical_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username
                or url.password or url.port not in (None, 80, 443)
                or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
                or "." not in url.hostname):
            raise ValueError
        query = sorted((k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() != "fbclid")
        if url.hostname.lower() in ("facebook.com", "www.facebook.com", "m.facebook.com"):
            item = re.fullmatch(r"/marketplace/item/(\d+)/?", url.path)
            if item:
                return f"https://www.facebook.com/marketplace/item/{item[1]}/"
        return urlunsplit((url.scheme, url.netloc.lower(), url.path.rstrip("/") or "/", urlencode(query), ""))
    except ValueError:
        raise ValueError("Expected a public HTTP(S) listing URL") from None


class ListingInput(Contract):
    external_id: Short | None = None
    url: str = Field(max_length=2048)
    title: Short | None = None
    description: str | None = Field(default=None, max_length=4000)
    price: Money | None = None
    currency: Currency | None = None
    condition: Short | None = None
    location: Short | None = None
    distance_km: float | None = Field(default=None, ge=0, le=20000)
    distance_from: Short | None = None
    listed_at: datetime | None = None
    observed_at: datetime | None = None
    image_url: str | None = Field(default=None, max_length=2048)

    @field_validator("url", "image_url")
    @classmethod
    def urls(cls, value):
        return canonical_url(value) if value is not None else None

    @field_validator("listed_at", "observed_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Timestamps require a UTC offset")
        return value.astimezone(timezone.utc) if value is not None else None


class ListingCandidate(ListingInput):
    source: Source
    observed_at: datetime


@dataclass(frozen=True)
class Capabilities:
    id: Source
    polling: bool
    description: str


@dataclass
class Page:
    listings: list[ListingCandidate]
    cursor: str | None = None


class IListingSource(Protocol):
    capabilities: Capabilities

    async def retrieve(self, watchlist: Watchlist, cursor: str | None, now: datetime) -> Page: ...


class Notifier(Protocol):
    async def send(self, message: str) -> None: ...


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def words(haystack: str, needle: str) -> bool:
    return bool(normalize(needle)) and f" {normalize(needle)} " in f" {haystack} "


def match(w: Watchlist, c: ListingCandidate, now: datetime) -> dict:
    reasons, unknown, rejected = [], [], []
    haystack = normalize(f"{c.title or ''} {c.description or ''}")
    for term in (w.phrase, *w.include):
        if term.strip():
            if words(haystack, term):
                reasons.append(f"Includes {term}")
            elif not c.title:
                unknown.append(f"text for {term}")
            else:
                rejected.append(f"Missing {term}")
    for term in w.exclude:
        if words(haystack, term):
            rejected.append(f"Excluded keyword {term}")
    if c.price is None:
        unknown.append("price")
    else:
        if c.price > w.max_price or (w.min_price is not None and c.price < w.min_price):
            rejected.append("Outside price range")
        else:
            reasons.append("Within price range")
    if c.currency is None:
        unknown.append("currency")
    elif c.currency != w.currency:
        rejected.append("Currency differs")
    if w.location:
        if w.radius_km is None:
            if not c.location:
                unknown.append("location")
            elif normalize(c.location) != normalize(w.location):
                rejected.append("Location differs")
            else:
                reasons.append("Location matches")
        elif c.distance_km is None or normalize(c.distance_from or "") != normalize(w.location):
            unknown.append("distance from watchlist location")
        elif c.distance_km > w.radius_km:
            rejected.append("Outside radius")
        else:
            reasons.append("Within radius")
    age = max(0, (now - c.listed_at).total_seconds() / 3600) if c.listed_at else None
    if w.max_age_hours is not None:
        if age is None:
            unknown.append("posted time")
        elif age > w.max_age_hours:
            rejected.append("Listing too old")
        else:
            reasons.append("Within age limit")
    if w.conditions:
        if not c.condition:
            unknown.append("condition")
        elif normalize(c.condition) not in {normalize(condition) for condition in w.conditions}:
            rejected.append("Condition differs")
        else:
            reasons.append("Condition matches")
    price, relevance, freshness, condition = w.weights
    price_factor = (float(1 - c.price / w.max_price) if w.max_price else float(c.price == 0)) if c.price is not None and c.currency == w.currency else 0
    breakdown = {
        "price": round(price * max(0, price_factor)),
        "relevance": relevance if not rejected and not any(x.startswith("text for ") for x in unknown) else 0,
        "freshness": round(freshness * max(0, 1 - age / (w.max_age_hours or 168))) if age is not None else 0,
        "condition": condition if c.condition and not any(x == "Condition differs" for x in rejected) else 0,
    }
    hard_unknown = bool(unknown)
    for field in ("price", "currency", "condition", "location", "listed_at"):
        label = "posted time" if field == "listed_at" else field
        if getattr(c, field) is None and label not in unknown:
            unknown.append(label)
    if rejected or (hard_unknown and not w.review_uncertain):
        status = "rejected"
    elif hard_unknown:
        status = "uncertain"
    else:
        status = "confirmed"
    return dict(status=status, reasons=reasons, unknown=unknown, rejected=rejected,
                score=sum(breakdown.values()), breakdown=breakdown)


def seller_draft(title: str | None, *, free: bool = False) -> str:
    title = normalize(title or "")
    if free or words(title, "free"):
        return "Hi, is this still available? What area would pickup be in? Thanks!"
    if any(words(title, term) for term in ("hdd", "hard drive")):
        return "Hi, is this drive still available? Could you share its SMART health and power-on hours? Thanks!"
    if any(words(title, term) for term in ("gpu", "p100", "graphics card")):
        return "Hi, is this GPU still available? Has it been tested recently? Thanks!"
    return "Hi, is this item still available? Thanks!"
