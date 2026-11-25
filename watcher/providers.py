"""The bundled source generates deterministic, explicitly simulated data."""
from datetime import datetime, timedelta, timezone
import logging

import httpx

from .core import Capabilities, ListingCandidate, Page, Watchlist

logger = logging.getLogger("uvicorn.error.marketplace_watcher")


class ProviderFailure(Exception):
    def __init__(self, message: str, *, retryable=False, pause=False, retry_after=0.0):
        super().__init__(message)
        self.retryable = retryable
        self.pause = pause
        self.retry_after = retry_after


SOURCES = [
    Capabilities("fixture", True, "Simulated listings only. Advances on each successful poll."),
    Capabilities("manual", False, "User-entered details. Links are never fetched."),
    Capabilities("alert", False, "Authenticated forwarding endpoint. No live Facebook connector."),
]


class FixtureListingSource:
    capabilities = SOURCES[0]

    def __init__(self, step: int):
        self.step = step

    async def retrieve(self, watchlist: Watchlist, cursor: str | None, now: datetime) -> Page:
        def listing(key, title, price, **details):
            return ListingCandidate(source="fixture", external_id=key,
                                    url=f"https://demo.example/listing/{key}", title=title,
                                    price=price, currency="CAD", location="Halifax",
                                    distance_from="Halifax", observed_at=now, **details)

        posted = datetime(2024, 1, 1, tzinfo=timezone.utc)
        base = listing("hdd-baseline", "Seagate 8TB HDD", 45, condition="used",
                       distance_km=4, listed_at=posted)
        if self.step <= 1:
            return Page([base])
        deal = listing("hdd-deal", "WD 8TB HDD", 25 if self.step >= 3 else 40,
                       condition="used", distance_km=3, listed_at=posted + timedelta(days=1))
        if cursor is None:
            return Page([base, deal, deal], "more")
        return Page([
            listing("hdd-wrong", "8GB HDD broken", 10, distance_km=3),
            listing("hdd-unknown", "8TB HDD", None),
            listing("gpu", "Tesla P100 16GB GPU", 90, condition="used", distance_km=5),
            listing("desktop", "Dell OptiPlex", 30, distance_km=5),
            listing("free", "Free electronics", 0, distance_km=1),
        ])


class ConsoleNotifier:
    async def send(self, message: str) -> None:
        logger.info("Console notification\n%s", message)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        # The Bot API puts the credential in the request URL. Disable HTTP trace logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    async def send(self, message: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                             json={"chat_id": self.chat_id, "text": message[:4000],
                                                   "link_preview_options": {"is_disabled": True}})
        except httpx.RequestError:
            raise ProviderFailure("Telegram response unavailable", retryable=True) from None
        try:
            result = response.json()
        except ValueError:
            result = {}
        if response.is_success and result.get("ok") is True:
            return
        try:
            after = float(result.get("parameters", {}).get("retry_after",
                         response.headers.get("Retry-After", 0)))
        except (TypeError, ValueError):
            after = 0
        raise ProviderFailure(f"Telegram rejected delivery (HTTP {response.status_code})",
                              retryable=response.status_code == 429 or response.status_code >= 500,
                              retry_after=after)
