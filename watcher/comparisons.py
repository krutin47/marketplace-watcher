"""Gate C1: side-by-side comparison snapshots (handbook CMP-01..04). Deterministic; never recommends buying."""
from decimal import Decimal
import json
from typing import Annotated

from pydantic import Field, field_validator

from . import purchasing
from .calculations import bundle_capacities, detect_drive_bundle
from .core import Contract, Short
from .errors import ApiError

ALGORITHM = "comparison/1"
TB_PER_TIB = Decimal(2) ** 40 / Decimal(10) ** 12
ListingIds = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=2, max_length=5)]


def _unique(value):
    if len(set(value)) != len(value):
        raise ValueError("Listings must be distinct")
    return value


class ComparisonIn(Contract):
    title: Short | None = None
    project_id: int | None = Field(default=None, ge=1)
    listing_ids: ListingIds
    _distinct = field_validator("listing_ids")(_unique)


class ComparisonPatch(Contract):
    version: int = Field(ge=1)
    title: Short | None = None
    project_id: int | None = Field(default=None, ge=1)
    listing_ids: ListingIds | None = None
    _distinct = field_validator("listing_ids")(lambda cls, v: _unique(v) if v is not None else v)


def _capacity_tb(requirement):
    unit = (requirement["unit"] or "TB").upper()
    value = Decimal(requirement["value"])
    return value * TB_PER_TIB if unit == "TIB" else value if unit == "TB" else None


def evaluate(requirement, listing, capacities):
    """Return (result, basis). Only derivable attributes are evaluated; everything else stays unknown."""
    attribute, op = requirement["attribute"], requirement["operator"]
    if attribute in ("usable_capacity", "capacity") and op in (">=", "<="):
        target = _capacity_tb(requirement)
        if target is None:
            return "unknown", f"Unit {requirement['unit']} is not a storage unit"
        if not capacities:
            return "unknown", "No drives are stated as included, so capacity is unknown"
        best = capacities[0]
        usable = Decimal(best["usable_tb"])
        meets = usable >= target if op == ">=" else usable <= target
        return ("meets" if meets else "fails"), f"{best['layout']} of stated drives gives {best['usable_tb']} TB usable (calculated)"
    if attribute in ("price", "total_cost") and op in (">=", "<="):
        if listing["price"] is None:
            return "unknown", "Price is not stated"
        if requirement["unit"] and requirement["unit"].upper() != (listing["currency"] or ""):
            return "unknown", "Different currency; no conversion is made"
        price, target = Decimal(listing["price"]), Decimal(requirement["value"])
        meets = price <= target if op == "<=" else price >= target
        return ("meets" if meets else "fails"), f"Asking price {listing['price']} {listing['currency'] or ''}".strip()
    return "unknown", "Not evaluated automatically; verify with the seller or a manual"


def snapshot(db, listing_ids, project_id) -> dict:
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone() if project_id else None
    requirements = purchasing.requirements(db, project_id) if project else []
    summary = purchasing.budget_summary(db, project) if project else None
    entries = []
    for listing_id in listing_ids:
        row = db.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if row is None:
            entries.append({"listing_id": listing_id, "missing": True})
            continue
        data = json.loads(row["data"])
        observation = db.execute("SELECT id FROM observations WHERE listing_id=? ORDER BY observed DESC,id DESC LIMIT 1",
                                 (listing_id,)).fetchone()
        claims = [dict(e) for e in db.execute("""SELECT id,content,received_at,created FROM evidence
            WHERE listing_id=? AND kind='seller_claim' ORDER BY COALESCE(received_at,created) DESC LIMIT 5""", (listing_id,))]
        text = " ".join(filter(None, [data.get("title"), data.get("description"), *(c["content"] for c in claims)]))
        bundle = detect_drive_bundle(text)
        capacities = bundle_capacities(bundle) if bundle else []
        unknown = [label for key, label in (("price", "price"), ("currency", "currency"), ("condition", "condition"),
                                            ("location", "location"), ("listed_at", "posted time")) if data.get(key) is None]
        fit, outcome = [], None
        if project:
            for requirement in requirements:
                result, basis = evaluate(requirement, data, capacities)
                fit.append({"label": requirement["label"], "hardness": requirement["hardness"], "result": result, "basis": basis})
            hard = [f["result"] for f in fit if f["hardness"] == "hard"]
            outcome = ("blocked by a hard requirement" if "fails" in hard else "needs evidence" if "unknown" in hard
                       else "candidate" if hard else "no hard requirements set")
        share = None
        if summary and summary["remaining"] is not None and data.get("price") is not None \
                and data.get("currency") == summary["currency"] and Decimal(summary["remaining"]) > 0:
            share = f"{Decimal(data['price']) / Decimal(summary['remaining']) * 100:.0f}"
        entries.append({
            "listing_id": listing_id, "ref": f"listing:{listing_id}" + (f"@obs:{observation['id']}" if observation else ""),
            "title": data.get("title"), "source": data["source"], "simulated": data["source"] == "fixture",
            "url": data["url"], "price": data.get("price"), "currency": data.get("currency"),
            "condition": data.get("condition"), "location": data.get("location"), "last_seen": row["last_seen"],
            "unknown": unknown, "seller_claims": [{"ref": f"evidence:{c['id']}", "content": c["content"][:200]} for c in claims],
            "drives": ({"stated": True, "count": bundle["count"], "size_tb": bundle["size_tb"], "matched": bundle["matched"]}
                       if bundle else {"stated": False, "note": "Drive inclusion is not stated; assume none are included."}),
            "capacities": [{"layout": c["layout"], "usable_tb": c["usable_tb"], "usable_tib": c["usable_tib"],
                            "fault_tolerance": c["fault_tolerance"]} for c in capacities],
            "total_cost": {"amount": data.get("price"), "currency": data.get("currency"),
                           "basis": "Asking price only; taxes, shipping and missing parts are unknown."},
            "budget_share_percent": share, "requirements": fit, "outcome": outcome})
    currencies = {e["currency"] for e in entries if not e.get("missing") and e["currency"]}
    notes = ["Outcomes are deterministic checks of stated data, not purchase recommendations.",
             "Capacity uses calculated layouts from explicitly stated drives; filesystem overhead is not included."]
    if len(currencies) > 1:
        notes.append("Listings use different currencies; prices are not compared or converted.")
    if any(e.get("simulated") for e in entries):
        notes.append("Some listings are simulated fixtures.")
    return {"algorithm_version": ALGORITHM, "project": {"id": project["id"], "name": project["name"],
            "version": project["version"], "budget": summary} if project else None, "listings": entries, "notes": notes}


def register(app, store, clock):
    def require(db, identifier):
        row = db.execute("SELECT * FROM comparisons WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApiError(404, "comparison_not_found", "Comparison not found")
        return row

    def check_targets(db, listing_ids, project_id):
        for listing_id in listing_ids or []:
            if not db.execute("SELECT 1 FROM listings WHERE id=?", (listing_id,)).fetchone():
                raise ApiError(404, "listing_not_found", f"Listing {listing_id} not found")
        if project_id is not None and not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise ApiError(404, "project_not_found", "Project not found")

    def take_snapshot(db, row):
        data = snapshot(db, json.loads(row["listing_ids"]), row["project_id"])
        db.execute("INSERT INTO comparison_snapshots(comparison_id,algorithm_version,data,created) VALUES(?,?,?,?)",
                   (row["id"], ALGORITHM, json.dumps(data, sort_keys=True), clock()))

    def detail(db, row):
        result = dict(row, listing_ids=json.loads(row["listing_ids"]))
        snaps = db.execute("SELECT id,created,algorithm_version FROM comparison_snapshots WHERE comparison_id=? ORDER BY id DESC",
                           (row["id"],)).fetchall()
        latest = db.execute("SELECT * FROM comparison_snapshots WHERE comparison_id=? ORDER BY id DESC LIMIT 1",
                            (row["id"],)).fetchone()
        result["snapshot"] = dict(json.loads(latest["data"]), id=latest["id"], created=latest["created"]) if latest else None
        result["history"] = [dict(s) for s in snaps]
        result["stale_reasons"] = stale_reasons(db, row, latest)
        result["stale"] = bool(result["stale_reasons"])
        return result

    def stale_reasons(db, row, latest):
        if latest is None:
            return ["No snapshot yet"]
        taken, ids = latest["created"], json.loads(row["listing_ids"])
        marks = ",".join("?" * len(ids))
        reasons = []
        if row["updated"] > taken:
            reasons.append("The comparison's listings or project changed")
        if db.execute(f"SELECT 1 FROM listings WHERE id IN ({marks}) AND last_seen>?", (*ids, taken)).fetchone():
            reasons.append("A listing was observed again")
        if db.execute(f"SELECT 1 FROM evidence WHERE listing_id IN ({marks}) AND created>?", (*ids, taken)).fetchone():
            reasons.append("New seller updates or notes were added")
        if row["project_id"] and db.execute("SELECT 1 FROM projects WHERE id=? AND updated>?",
                                            (row["project_id"], taken)).fetchone():
            reasons.append("The project changed")
        return reasons

    @app.get("/api/comparisons")
    async def list_comparisons():
        with store.connect() as db:
            return [dict(r, listing_ids=json.loads(r["listing_ids"]))
                    for r in db.execute("SELECT * FROM comparisons ORDER BY updated DESC LIMIT 100")]

    @app.post("/api/comparisons", status_code=201)
    async def create_comparison(body: ComparisonIn):
        now = clock()
        with store.connect() as db:
            check_targets(db, body.listing_ids, body.project_id)
            identifier = db.execute("INSERT INTO comparisons(title,project_id,listing_ids,created,updated) VALUES(?,?,?,?,?)",
                                    (body.title or f"Comparison of {len(body.listing_ids)} listings", body.project_id,
                                     json.dumps(body.listing_ids), now, now)).lastrowid
            take_snapshot(db, require(db, identifier))
            purchasing.audit(db, "created", "comparison", identifier, {"listing_ids": body.listing_ids}, now)
            return detail(db, require(db, identifier))

    @app.get("/api/comparisons/{identifier}")
    async def get_comparison(identifier: int, snapshot_id: int | None = None):
        with store.connect() as db:
            result = detail(db, require(db, identifier))
            if snapshot_id is not None:
                snap = db.execute("SELECT * FROM comparison_snapshots WHERE id=? AND comparison_id=?",
                                  (snapshot_id, identifier)).fetchone()
                if snap is None:
                    raise ApiError(404, "snapshot_not_found", "Snapshot not found")
                result["snapshot"] = dict(json.loads(snap["data"]), id=snap["id"], created=snap["created"])
            return result

    @app.patch("/api/comparisons/{identifier}")
    async def patch_comparison(identifier: int, body: ComparisonPatch):
        now = clock()
        with store.connect() as db:
            row = require(db, identifier)
            if row["version"] != body.version:
                raise ApiError(409, "version_conflict", "Comparison changed since it was loaded; reload and retry")
            changes = body.model_dump(exclude_unset=True, exclude={"version"})
            check_targets(db, changes.get("listing_ids"), changes.get("project_id"))
            if "title" in changes and changes["title"] is None:
                raise ApiError(422, "validation_failed", "Title cannot be empty")
            if "listing_ids" in changes:
                changes["listing_ids"] = json.dumps(changes["listing_ids"])
            if changes:
                db.execute(f"UPDATE comparisons SET {''.join(f'{k}=?,' for k in changes)}version=version+1,updated=? WHERE id=?",
                           (*changes.values(), now, identifier))
            return detail(db, require(db, identifier))

    @app.post("/api/comparisons/{identifier}/refresh")
    async def refresh(identifier: int):
        with store.connect() as db:
            take_snapshot(db, require(db, identifier))
            return detail(db, require(db, identifier))

    @app.delete("/api/comparisons/{identifier}", status_code=204)
    async def delete_comparison(identifier: int):
        with store.connect() as db:
            require(db, identifier)
            db.execute("DELETE FROM comparisons WHERE id=?", (identifier,))
