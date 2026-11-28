"""Deterministic, read-only calculators (handbook CMP-04, T-18). Decimal arithmetic, explicit units and assumptions."""
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .core import Contract, Currency

TB = Decimal(10) ** 12
TIB = Decimal(2) ** 40
Layout = Literal["jbod", "stripe", "mirror", "raid5", "raid6", "raid10"]
DriveSize = Annotated[Decimal, Field(gt=0, le=1000, max_digits=8, decimal_places=3)]
COMMON_ASSUMPTIONS = [
    "Drive sizes are decimal terabytes (TB) as marketed; TiB is what most operating systems report.",
    "Filesystem, metadata and reserved-space overhead are not included.",
    "Redundancy is not a backup. Keep a separate copy of data you cannot lose.",
]


class CalculationError(ValueError):
    pass


def q(value: Decimal, places="0.01") -> str:
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


class StorageIn(Contract):
    drives_tb: list[DriveSize] = Field(min_length=1, max_length=64)
    layout: Layout
    mirror_width: int = Field(default=2, ge=2, le=4)


def storage(body: StorageIn) -> dict:
    drives = sorted(body.drives_tb)
    n, smallest = len(drives), drives[0]
    raw = sum(drives)
    mixed = len(set(drives)) > 1
    assumptions = list(COMMON_ASSUMPTIONS)
    minimum = {"jbod": 1, "stripe": 2, "mirror": body.mirror_width, "raid5": 3, "raid6": 4, "raid10": 4}[body.layout]
    if n < minimum:
        raise CalculationError(f"{body.layout} needs at least {minimum} drives; {n} given")
    if body.layout in ("jbod", "stripe"):
        usable, tolerance = raw, "None. Any drive failure loses data" + (" on that drive." if body.layout == "jbod" else " on the whole array.")
        if body.layout == "stripe" and mixed:
            usable = smallest * n
            assumptions.append(f"A stripe uses the smallest member ({smallest} TB) on every drive.")
    else:
        if body.layout == "mirror":
            if n % body.mirror_width:
                raise CalculationError(f"{n} drives cannot form mirror groups of {body.mirror_width}")
            groups = n // body.mirror_width
            usable, tolerance = smallest * groups, f"One drive per {body.mirror_width}-way mirror group ({body.mirror_width - 1} per group)."
        elif body.layout == "raid5":
            usable, tolerance = smallest * (n - 1), "Any one drive."
        elif body.layout == "raid6":
            usable, tolerance = smallest * (n - 2), "Any two drives."
        else:
            if n % 2:
                raise CalculationError("RAID10 needs an even number of drives")
            usable, tolerance = smallest * (n // 2), "One drive per mirrored pair."
        if mixed:
            wasted = raw - smallest * n
            assumptions.append(f"Mixed sizes: each member is limited to the smallest drive ({smallest} TB), "
                               f"leaving {q(wasted)} TB unused unless partitioned separately.")
    return {"layout": body.layout, "drives": n, "drive_sizes_tb": [str(d) for d in body.drives_tb],
            "raw_tb": q(raw), "usable_tb": q(usable), "usable_tib": q(usable * TB / TIB),
            "fault_tolerance": tolerance, "assumptions": assumptions}


class EnergyIn(Contract):
    watts: Annotated[Decimal, Field(ge=0, le=100_000, max_digits=9, decimal_places=2)]
    hours_per_day: Annotated[Decimal, Field(gt=0, le=24, max_digits=4, decimal_places=2)] = Decimal(24)
    rate_per_kwh: Annotated[Decimal, Field(ge=0, le=10, max_digits=7, decimal_places=5)] | None = None
    currency: Currency | None = None

    @model_validator(mode="after")
    def rate_currency(self):
        if (self.rate_per_kwh is None) != (self.currency is None):
            raise ValueError("Electricity rate and currency go together")
        return self


def energy(body: EnergyIn) -> dict:
    hours = body.hours_per_day * 365
    kwh = body.watts / 1000 * hours
    return {"watts": str(body.watts), "hours_per_year": q(hours, "1"), "kwh_per_year": q(kwh),
            "rate_per_kwh": str(body.rate_per_kwh) if body.rate_per_kwh is not None else None,
            "rate_basis": "supplied by you" if body.rate_per_kwh is not None else "not supplied; cost not calculated",
            "annual_cost": q(kwh * body.rate_per_kwh) if body.rate_per_kwh is not None else None,
            "currency": body.currency,
            "formula": "watts / 1000 × hours per year × rate per kWh",
            "assumptions": ["Constant average draw; measure at the wall for real figures.",
                            "365-day year; taxes and delivery charges are not included unless in your rate."]}


class BudgetItem(Contract):
    label: Annotated[str, Field(min_length=1, max_length=160)]
    amount: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
    currency: Currency


class BudgetIn(Contract):
    budget: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
    currency: Currency
    items: list[BudgetItem] = Field(min_length=1, max_length=50)


def budget(body: BudgetIn) -> dict:
    other = sorted({i.currency for i in body.items if i.currency != body.currency})
    if other:
        raise CalculationError(f"Items in {', '.join(other)} differ from the budget currency; no conversion is made")
    total = sum(i.amount for i in body.items)
    return {"currency": body.currency, "budget": q(body.budget), "total": q(total),
            "remaining": q(body.budget - total), "over_budget": total > body.budget,
            "items": [{"label": i.label, "amount": q(i.amount)} for i in body.items]}


BUNDLE = re.compile(r"\b(\d{1,2})\s*[x×]\s*(\d{1,3}(?:\.\d{1,2})?)\s*(TB|GB)\b", re.IGNORECASE)


def detect_drive_bundle(text: str) -> dict | None:
    """Find an explicit 'N x SIZE' drive statement. Absence means inclusion is not stated, never 'included'."""
    match = BUNDLE.search(text or "")
    if not match:
        return None
    count, size, unit = int(match[1]), Decimal(match[2]), match[3].upper()
    size_tb = size if unit == "TB" else size / 1000
    if count < 1 or size_tb <= 0:
        return None
    return {"count": count, "size_tb": str(size_tb), "matched": match[0]}


def bundle_capacities(bundle: dict) -> list[dict]:
    drives = [Decimal(bundle["size_tb"])] * bundle["count"]
    results = []
    for layout in ("raid6", "raid5", "mirror", "raid10"):
        try:
            results.append(storage(StorageIn(drives_tb=drives, layout=layout)))
        except CalculationError:
            continue
    return results


def register(app):
    from .errors import ApiError

    def run(function, body):
        try:
            return function(body)
        except CalculationError as exc:
            raise ApiError(422, "invalid_calculation", str(exc)) from None

    @app.post("/api/calculations/storage")
    async def storage_route(body: StorageIn):
        return run(storage, body)

    @app.post("/api/calculations/energy")
    async def energy_route(body: EnergyIn):
        return run(energy, body)

    @app.post("/api/calculations/budget")
    async def budget_route(body: BudgetIn):
        return run(budget, body)
