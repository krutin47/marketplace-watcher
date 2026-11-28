"""Gate B2: projects, requirements, budgets, inventory, purchases and user-approved memory.

Every mutation here is a user action through the HTTP API with validation and an audit event. Nothing in this
module is reachable from an assistant run (ADR-06).
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import Field, StringConstraints, field_validator, model_validator

from .core import Contract, Currency, Money, Short
from .errors import ApiError

Text = Annotated[str, Field(max_length=4000)]
Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ProjectStatus = Literal["active", "paused", "completed", "archived"]
Role = Literal["candidate", "shortlisted", "rejected", "purchased"]


def to_minor(value: Decimal | None) -> int | None:
    return None if value is None else int(value * 100)


def from_minor(value: int | None) -> str | None:
    return None if value is None else f"{Decimal(value) / 100:.2f}"


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "fact"


def audit(db, action, entity, entity_id, data, now, actor="user"):
    db.execute("INSERT INTO audit_events(actor,action,entity,entity_id,data,created) VALUES(?,?,?,?,?,?)",
               (actor, action, entity, entity_id, json.dumps(data, sort_keys=True, default=str), now))


class Requirement(Contract):
    label: Short
    attribute: Key
    operator: Literal[">=", "<=", "=", "!=", "contains", "present"]
    value: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] | None = None
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=24)] | None = None
    hardness: Literal["hard", "soft"]
    weight: int = Field(default=0, ge=0, le=100)
    evidence_required: bool = False
    notes: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def consistent(self):
        if self.operator != "present" and not self.value:
            raise ValueError("A value is required unless the operator is 'present'")
        if self.operator in (">=", "<="):
            try:
                Decimal(self.value)
            except Exception:
                raise ValueError("Numeric comparisons need a numeric value") from None
        if self.hardness == "hard" and self.weight:
            raise ValueError("Hard requirements are pass/fail and take no weight")
        return self


class Requirements(Contract):
    version: int = Field(ge=1)
    requirements: list[Requirement] = Field(max_length=40)

    @model_validator(mode="after")
    def weights(self):
        if sum(r.weight for r in self.requirements) > 100:
            raise ValueError("Preference weights must not exceed 100 in total")
        return self


class ProjectIn(Contract):
    name: Short
    description: Text = ""
    status: ProjectStatus = "active"
    target_date: date | None = None
    budget: Money | None = None
    currency: Currency = "CAD"
    notes: Text = ""


class ProjectPatch(Contract):
    version: int = Field(ge=1)
    name: Short | None = None
    description: Text | None = None
    status: ProjectStatus | None = None
    target_date: date | None = None
    budget: Money | None = None
    notes: Text | None = None


class BudgetChange(Contract):
    version: int = Field(ge=1)
    budget: Money | None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class ProjectListingIn(Contract):
    listing_id: int = Field(ge=1)
    role: Role = "candidate"
    note: Annotated[str, Field(max_length=1000)] = ""


class ProjectListingPatch(Contract):
    role: Role | None = None
    note: Annotated[str, Field(max_length=1000)] | None = None


class InventoryIn(Contract):
    name: Short
    category: Key
    manufacturer: Short | None = None
    model: Short | None = None
    quantity: int = Field(default=1, ge=0, le=100_000)
    condition: Short | None = None
    lifecycle: Literal["in_use", "spare", "retired", "sold"] = "in_use"
    acquisition_cost: Money | None = None
    acquisition_currency: Currency | None = None
    acquired_on: date | None = None
    serial: Short | None = None
    attributes: dict[Key, str | int | float | bool | None] = Field(default_factory=dict, max_length=40)
    attributes_schema: int = Field(default=1, ge=1, le=1000)
    notes: Text = ""

    @model_validator(mode="after")
    def cost_currency(self):
        if (self.acquisition_cost is None) != (self.acquisition_currency is None):
            raise ValueError("Acquisition cost and currency go together")
        return self


class InventoryPatch(InventoryIn):
    version: int = Field(ge=1)
    name: Short | None = None
    category: Key | None = None
    quantity: int | None = Field(default=None, ge=0, le=100_000)
    lifecycle: Literal["in_use", "spare", "retired", "sold"] | None = None
    attributes: dict[Key, str | int | float | bool | None] | None = Field(default=None, max_length=40)
    attributes_schema: int | None = Field(default=None, ge=1, le=1000)
    notes: Text | None = None


class AllocationIn(Contract):
    quantity: int = Field(ge=1, le=100_000)
    override_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)] | None = None


class PurchaseIn(Contract):
    confirm: Literal[True]
    description: Short
    amount: Money
    currency: Currency
    purchased_at: datetime
    listing_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    reason: Annotated[str, Field(max_length=1000)] = ""

    @field_validator("purchased_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timestamps require a UTC offset")
        return value


class FactIn(Contract):
    scope: Literal["global", "project", "inventory", "listing", "decision"]
    project_id: int | None = Field(default=None, ge=1)
    listing_id: int | None = Field(default=None, ge=1)
    inventory_item_id: int | None = Field(default=None, ge=1)
    key: Key
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=24)] | None = None
    source: Short = "entered by user"

    @model_validator(mode="after")
    def target(self):
        needs = {"project": "project_id", "listing": "listing_id", "inventory": "inventory_item_id"}
        if self.scope in needs and getattr(self, needs[self.scope]) is None:
            raise ValueError(f"A {self.scope} fact needs {needs[self.scope]}")
        if self.scope == "decision" and self.project_id is None and self.listing_id is None:
            raise ValueError("A decision needs a project or listing")
        if self.scope == "global" and any((self.project_id, self.listing_id, self.inventory_item_id)):
            raise ValueError("A global fact has no target")
        return self


class FactPatch(Contract):
    version: int = Field(ge=1)
    action: Literal["approve", "reject", "correct"]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=24)] | None = None

    @model_validator(mode="after")
    def correction(self):
        if self.action == "correct" and self.value is None:
            raise ValueError("A correction needs a value")
        return self


# ----- read models shared with the assistant context builder -----
def project_row(row) -> dict:
    data = dict(row)
    data["budget"] = from_minor(data.pop("budget_minor"))
    return data


def budget_summary(db, project) -> dict:
    """Actual spend comes from explicitly confirmed purchases only. No currency conversion (handbook section 11)."""
    rows = db.execute("SELECT currency, SUM(amount_minor) total FROM purchases WHERE project_id=? GROUP BY currency",
                      (project["id"],)).fetchall()
    actual = next((r["total"] for r in rows if r["currency"] == project["currency"]), 0)
    budget = project["budget_minor"]
    return {"currency": project["currency"], "budget": from_minor(budget), "actual": from_minor(actual),
            "committed": None, "committed_note": "Accepted offers are not tracked yet, so commitments are unknown.",
            "remaining": from_minor(budget - actual) if budget is not None else None,
            "unconverted": [{"currency": r["currency"], "amount": from_minor(r["total"])}
                            for r in rows if r["currency"] != project["currency"]]}


def requirements(db, project_id) -> list[dict]:
    rows = db.execute("SELECT * FROM project_requirements WHERE project_id=? ORDER BY position", (project_id,))
    return [dict(r, evidence_required=bool(r["evidence_required"])) for r in rows]


def item_row(row, *, private=False) -> dict:
    data = dict(row)
    data["acquisition_cost"] = from_minor(data.pop("acquisition_cost_minor"))
    data["attributes"] = json.loads(data["attributes"])
    if not private:
        data["serial"] = "(stored privately)" if data["serial"] else None
    return data


def allocations(db, project_id) -> list[dict]:
    return [dict(r) for r in db.execute("""SELECT a.quantity allocated, a.override_reason, i.id, i.name, i.category,
        i.manufacturer, i.model, i.condition, i.attributes FROM inventory_allocations a
        JOIN inventory_items i ON i.id=a.inventory_item_id WHERE a.project_id=? ORDER BY i.name""", (project_id,))]


def approved_facts(db, project_id, listing_id) -> list[dict]:
    """Facts in scope for one conversation turn. Suggested, rejected and superseded facts are never used."""
    return [dict(r) for r in db.execute("""SELECT id,scope,key,value,unit,source FROM memory_facts
        WHERE status='approved' AND (
          scope='global'
          OR (scope IN ('project','decision') AND project_id IS ? AND project_id IS NOT NULL)
          OR (scope IN ('listing','decision') AND listing_id=?)
          OR (scope='inventory' AND inventory_item_id IN (
                SELECT inventory_item_id FROM inventory_allocations WHERE project_id IS ?)))
        ORDER BY scope, key, id""", (project_id, listing_id, project_id))]


def suggest_fact(db, fact: FactIn, now, source="assistant suggestion") -> int:
    """Record an assistant-originated suggestion. It has no effect until the user approves it."""
    fact_id = db.execute("""INSERT INTO memory_facts(scope,project_id,listing_id,inventory_item_id,key,value,unit,
        source,origin,confidence,status,created,updated) VALUES(?,?,?,?,?,?,?,?,'assistant','inferred','suggested',?,?)""",
        (fact.scope, fact.project_id, fact.listing_id, fact.inventory_item_id, normalize_key(fact.key), fact.value,
         fact.unit, source, now, now)).lastrowid
    audit(db, "suggested", "memory_fact", fact_id, {"key": fact.key}, now, actor="assistant")
    return fact_id


def register(app, store, clock):
    def require(db, table, identifier, code):
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApiError(404, code, "Record not found")
        return row

    def check_version(row, version):
        if row["version"] != version:
            raise ApiError(409, "version_conflict", "This record changed since it was loaded; reload and retry")

    def project_detail(db, row):
        data = project_row(row)
        data["requirements"] = requirements(db, row["id"])
        data["budget_summary"] = budget_summary(db, row)
        data["budget_changes"] = [dict(r, previous=from_minor(r["previous_minor"]), new=from_minor(r["new_minor"]))
                                  for r in db.execute("SELECT * FROM budget_changes WHERE project_id=? ORDER BY id DESC",
                                                      (row["id"],))]
        data["listings"] = []
        for link in db.execute("""SELECT pl.*, l.data FROM project_listings pl JOIN listings l ON l.id=pl.listing_id
                                  WHERE pl.project_id=? ORDER BY pl.created DESC""", (row["id"],)):
            listing = json.loads(link["data"])
            data["listings"].append({"listing_id": link["listing_id"], "role": link["role"], "note": link["note"],
                                     "title": listing.get("title"), "price": listing.get("price"),
                                     "currency": listing.get("currency"), "source": listing.get("source")})
        data["allocations"] = [dict(a, attributes=json.loads(a["attributes"])) for a in allocations(db, row["id"])]
        data["purchases"] = [dict(p, amount=from_minor(p["amount_minor"]))
                             for p in db.execute("SELECT * FROM purchases WHERE project_id=? ORDER BY purchased_at DESC",
                                                 (row["id"],))]
        return data

    # ----- projects (PROJ-01..06) -----
    @app.get("/api/projects")
    async def list_projects(status: ProjectStatus | None = None):
        with store.connect() as db:
            rows = db.execute("SELECT * FROM projects WHERE (? IS NULL OR status=?) ORDER BY status='archived', updated DESC",
                              (status, status)).fetchall()
            return [dict(project_row(r), budget_summary=budget_summary(db, r)) for r in rows]

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectIn):
        now = clock()
        with store.connect() as db:
            project_id = db.execute("""INSERT INTO projects(name,description,status,target_date,budget_minor,currency,
                notes,created,updated) VALUES(?,?,?,?,?,?,?,?,?)""",
                (body.name, body.description, body.status, body.target_date and body.target_date.isoformat(),
                 to_minor(body.budget), body.currency, body.notes, now, now)).lastrowid
            audit(db, "created", "project", project_id, {"name": body.name, "budget": body.budget}, now)
            return project_detail(db, require(db, "projects", project_id, "project_not_found"))

    @app.get("/api/projects/{identifier}")
    async def get_project(identifier: int):
        with store.connect() as db:
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.patch("/api/projects/{identifier}")
    async def patch_project(identifier: int, body: ProjectPatch):
        now = clock()
        with store.connect() as db:
            row = require(db, "projects", identifier, "project_not_found")
            check_version(row, body.version)
            changes = body.model_dump(exclude_unset=True, exclude={"version"})
            if "budget" in changes:
                if row["budget_minor"] is not None and to_minor(changes["budget"]) != row["budget_minor"]:
                    raise ApiError(409, "budget_change_requires_reason",
                                   "Change an existing budget with a recorded reason (budget override)")
                changes["budget_minor"] = to_minor(changes.pop("budget"))
            if "target_date" in changes and changes["target_date"] is not None:
                changes["target_date"] = changes["target_date"].isoformat()
            for required in ("name", "status"):
                if required in changes and changes[required] is None:
                    raise ApiError(422, "validation_failed", f"{required} cannot be empty")
            if changes:
                db.execute(f"UPDATE projects SET {''.join(f'{k}=?,' for k in changes)}version=version+1,updated=? WHERE id=?",
                           (*changes.values(), now, identifier))
                audit(db, "updated", "project", identifier, changes, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.post("/api/projects/{identifier}/budget")
    async def change_budget(identifier: int, body: BudgetChange):
        now = clock()
        with store.connect() as db:
            row = require(db, "projects", identifier, "project_not_found")
            check_version(row, body.version)
            new = to_minor(body.budget)
            db.execute("INSERT INTO budget_changes(project_id,previous_minor,new_minor,reason,created) VALUES(?,?,?,?,?)",
                       (identifier, row["budget_minor"], new, body.reason, now))
            db.execute("UPDATE projects SET budget_minor=?,version=version+1,updated=? WHERE id=?", (new, now, identifier))
            audit(db, "budget_changed", "project", identifier,
                  {"previous": from_minor(row["budget_minor"]), "new": from_minor(new), "reason": body.reason}, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.put("/api/projects/{identifier}/requirements")
    async def put_requirements(identifier: int, body: Requirements):
        now = clock()
        with store.connect() as db:
            row = require(db, "projects", identifier, "project_not_found")
            check_version(row, body.version)
            db.execute("DELETE FROM project_requirements WHERE project_id=?", (identifier,))
            db.executemany("""INSERT INTO project_requirements(project_id,position,label,attribute,operator,value,unit,
                hardness,weight,evidence_required,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [(identifier, index, r.label, normalize_key(r.attribute), r.operator, r.value, r.unit, r.hardness,
                  r.weight, r.evidence_required, r.notes) for index, r in enumerate(body.requirements)])
            db.execute("UPDATE projects SET version=version+1,updated=? WHERE id=?", (now, identifier))
            audit(db, "requirements_replaced", "project", identifier, {"count": len(body.requirements)}, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.post("/api/projects/{identifier}/listings", status_code=201)
    async def link_listing(identifier: int, body: ProjectListingIn):
        now = clock()
        with store.connect() as db:
            require(db, "projects", identifier, "project_not_found")
            require(db, "listings", body.listing_id, "listing_not_found")
            if db.execute("SELECT 1 FROM project_listings WHERE project_id=? AND listing_id=?",
                          (identifier, body.listing_id)).fetchone():
                raise ApiError(409, "already_linked", "This listing is already linked to the project")
            db.execute("INSERT INTO project_listings VALUES(?,?,?,?,?)",
                       (identifier, body.listing_id, body.role, body.note, now))
            audit(db, "linked_listing", "project", identifier, {"listing_id": body.listing_id, "role": body.role}, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.patch("/api/projects/{identifier}/listings/{listing_id}")
    async def patch_link(identifier: int, listing_id: int, body: ProjectListingPatch):
        now = clock()
        with store.connect() as db:
            link = db.execute("SELECT * FROM project_listings WHERE project_id=? AND listing_id=?",
                              (identifier, listing_id)).fetchone()
            if link is None:
                raise ApiError(404, "link_not_found", "Listing is not linked to this project")
            changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
            if changes:
                db.execute(f"UPDATE project_listings SET {','.join(f'{k}=?' for k in changes)} WHERE project_id=? AND listing_id=?",
                           (*changes.values(), identifier, listing_id))
                audit(db, "updated_link", "project", identifier, {"listing_id": listing_id, "from": link["role"], **changes}, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.delete("/api/projects/{identifier}/listings/{listing_id}", status_code=204)
    async def unlink_listing(identifier: int, listing_id: int):
        with store.connect() as db:
            if not db.execute("DELETE FROM project_listings WHERE project_id=? AND listing_id=?",
                              (identifier, listing_id)).rowcount:
                raise ApiError(404, "link_not_found", "Listing is not linked to this project")
            audit(db, "unlinked_listing", "project", identifier, {"listing_id": listing_id}, clock())

    @app.get("/api/listings/{identifier}/projects")
    async def listing_projects(identifier: int):
        with store.connect() as db:
            require(db, "listings", identifier, "listing_not_found")
            return [dict(r) for r in db.execute("""SELECT p.id, p.name, p.status, pl.role FROM project_listings pl
                JOIN projects p ON p.id=pl.project_id WHERE pl.listing_id=? ORDER BY p.name""", (identifier,))]

    # ----- inventory (INV-01..06) -----
    @app.get("/api/inventory")
    async def list_inventory(category: Key | None = None, include_retired: bool = False):
        with store.connect() as db:
            rows = db.execute("""SELECT i.*, COALESCE((SELECT SUM(quantity) FROM inventory_allocations a
                WHERE a.inventory_item_id=i.id),0) allocated FROM inventory_items i
                WHERE (? IS NULL OR category=?) AND (? OR lifecycle IN ('in_use','spare')) ORDER BY category, name""",
                (category and normalize_key(category), category and normalize_key(category), include_retired)).fetchall()
            return [item_row(r) for r in rows]

    @app.post("/api/inventory", status_code=201)
    async def create_item(body: InventoryIn):
        now = clock()
        with store.connect() as db:
            item_id = db.execute("""INSERT INTO inventory_items(name,category,manufacturer,model,quantity,condition,
                lifecycle,acquisition_cost_minor,acquisition_currency,acquired_on,serial,attributes,attributes_schema,
                notes,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (body.name, normalize_key(body.category), body.manufacturer, body.model, body.quantity, body.condition,
                 body.lifecycle, to_minor(body.acquisition_cost), body.acquisition_currency,
                 body.acquired_on and body.acquired_on.isoformat(), body.serial, json.dumps(body.attributes),
                 body.attributes_schema, body.notes, now, now)).lastrowid
            audit(db, "created", "inventory_item", item_id, {"name": body.name, "quantity": body.quantity}, now)
            return item_row(require(db, "inventory_items", item_id, "inventory_not_found"))

    @app.get("/api/inventory/{identifier}")
    async def get_item(identifier: int, reveal_serial: bool = False):
        with store.connect() as db:
            data = item_row(require(db, "inventory_items", identifier, "inventory_not_found"), private=reveal_serial)
            data["allocations"] = [dict(r) for r in db.execute("""SELECT a.project_id, p.name project, a.quantity,
                a.override_reason FROM inventory_allocations a JOIN projects p ON p.id=a.project_id
                WHERE a.inventory_item_id=?""", (identifier,))]
            return data

    @app.patch("/api/inventory/{identifier}")
    async def patch_item(identifier: int, body: InventoryPatch):
        now = clock()
        with store.connect() as db:
            row = require(db, "inventory_items", identifier, "inventory_not_found")
            check_version(row, body.version)
            changes: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"version"})
            for required in ("name", "category", "quantity", "lifecycle"):
                if required in changes and changes[required] is None:
                    raise ApiError(422, "validation_failed", f"{required} cannot be empty")
            if "quantity" in changes:
                allocated = db.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory_allocations WHERE inventory_item_id=?",
                                       (identifier,)).fetchone()[0]
                if changes["quantity"] < allocated:
                    raise ApiError(409, "quantity_below_allocated",
                                   f"{allocated} are allocated to projects; release allocations first")
            if "category" in changes:
                changes["category"] = normalize_key(changes["category"])
            if "acquisition_cost" in changes:
                changes["acquisition_cost_minor"] = to_minor(changes.pop("acquisition_cost"))
            if "attributes" in changes:
                changes["attributes"] = json.dumps(changes["attributes"] or {})
            if changes.get("acquired_on") is not None:
                changes["acquired_on"] = changes["acquired_on"].isoformat()
            merged_cost = changes.get("acquisition_cost_minor", row["acquisition_cost_minor"])
            merged_currency = changes.get("acquisition_currency", row["acquisition_currency"])
            if (merged_cost is None) != (merged_currency is None):
                raise ApiError(422, "validation_failed", "Acquisition cost and currency go together")
            if changes:
                db.execute(f"UPDATE inventory_items SET {''.join(f'{k}=?,' for k in changes)}version=version+1,updated=? WHERE id=?",
                           (*changes.values(), now, identifier))
                audit(db, "updated", "inventory_item", identifier, {k: v for k, v in changes.items() if k != "serial"}, now)
            return item_row(require(db, "inventory_items", identifier, "inventory_not_found"))

    @app.delete("/api/inventory/{identifier}", status_code=204)
    async def delete_item(identifier: int):
        with store.connect() as db:
            require(db, "inventory_items", identifier, "inventory_not_found")
            if db.execute("SELECT 1 FROM inventory_allocations WHERE inventory_item_id=?", (identifier,)).fetchone():
                raise ApiError(409, "item_allocated", "Release project allocations before deleting this item")
            db.execute("DELETE FROM inventory_items WHERE id=?", (identifier,))
            audit(db, "deleted", "inventory_item", identifier, {}, clock())

    @app.put("/api/projects/{identifier}/allocations/{item_id}")
    async def allocate(identifier: int, item_id: int, body: AllocationIn):
        now = clock()
        with store.connect() as db:
            require(db, "projects", identifier, "project_not_found")
            item = require(db, "inventory_items", item_id, "inventory_not_found")
            elsewhere = db.execute("""SELECT COALESCE(SUM(quantity),0) FROM inventory_allocations
                WHERE inventory_item_id=? AND project_id!=?""", (item_id, identifier)).fetchone()[0]
            available = item["quantity"] - elsewhere
            if body.quantity > available and not body.override_reason:
                raise ApiError(409, "allocation_exceeds_quantity",
                               f"Only {max(available, 0)} of {item['quantity']} are unallocated. Give an override reason to exceed it.")
            db.execute("""INSERT INTO inventory_allocations VALUES(?,?,?,?,?) ON CONFLICT(project_id,inventory_item_id)
                DO UPDATE SET quantity=excluded.quantity, override_reason=excluded.override_reason""",
                (identifier, item_id, body.quantity, body.override_reason if body.quantity > available else None, now))
            audit(db, "allocated", "inventory_item", item_id,
                  {"project_id": identifier, "quantity": body.quantity, "override_reason": body.override_reason}, now)
            return project_detail(db, require(db, "projects", identifier, "project_not_found"))

    @app.delete("/api/projects/{identifier}/allocations/{item_id}", status_code=204)
    async def release(identifier: int, item_id: int):
        with store.connect() as db:
            if not db.execute("DELETE FROM inventory_allocations WHERE project_id=? AND inventory_item_id=?",
                              (identifier, item_id)).rowcount:
                raise ApiError(404, "allocation_not_found", "No allocation to release")
            audit(db, "released", "inventory_item", item_id, {"project_id": identifier}, clock())

    # ----- purchases (INV-03, NEG-06): explicit confirmation, never creates inventory -----
    @app.post("/api/purchases", status_code=201)
    async def record_purchase(body: PurchaseIn):
        now = clock()
        if body.purchased_at.timestamp() > now + 300:
            raise ApiError(422, "future_timestamp", "Purchase time is in the future")
        with store.connect() as db:
            if body.listing_id is not None:
                require(db, "listings", body.listing_id, "listing_not_found")
            if body.project_id is not None:
                require(db, "projects", body.project_id, "project_not_found")
            purchase_id = db.execute("""INSERT INTO purchases(listing_id,project_id,description,amount_minor,currency,
                purchased_at,reason,created) VALUES(?,?,?,?,?,?,?,?)""",
                (body.listing_id, body.project_id, body.description, to_minor(body.amount), body.currency,
                 body.purchased_at.timestamp(), body.reason, now)).lastrowid
            if body.listing_id is not None and body.project_id is not None:
                db.execute("""INSERT INTO project_listings VALUES(?,?,'purchased','',?)
                    ON CONFLICT(project_id,listing_id) DO UPDATE SET role='purchased'""",
                    (body.project_id, body.listing_id, now))
            audit(db, "recorded", "purchase", purchase_id, body.model_dump(mode="json", exclude={"confirm"}), now)
            row = db.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
            return dict(row, amount=from_minor(row["amount_minor"]))

    @app.get("/api/purchases")
    async def list_purchases(limit: int = Query(50, ge=1, le=100)):
        with store.connect() as db:
            return [dict(r, amount=from_minor(r["amount_minor"]))
                    for r in db.execute("SELECT * FROM purchases ORDER BY purchased_at DESC LIMIT ?", (limit,))]

    # ----- memory (section 15) -----
    def fact(db, identifier):
        return require(db, "memory_facts", identifier, "fact_not_found")

    @app.get("/api/memory")
    async def list_facts(status: Literal["suggested", "approved", "rejected", "superseded"] | None = None,
                         project_id: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=200)):
        with store.connect() as db:
            return [dict(r) for r in db.execute("""SELECT * FROM memory_facts WHERE (? IS NULL OR status=?)
                AND (? IS NULL OR project_id=?) ORDER BY status='suggested' DESC, updated DESC LIMIT ?""",
                (status, status, project_id, project_id, limit))]

    @app.get("/api/memory/export")
    async def export_facts():
        with store.connect() as db:
            return {"exported_at": datetime.fromtimestamp(clock(), timezone.utc).isoformat(),
                    "facts": [dict(r) for r in db.execute("SELECT * FROM memory_facts WHERE status='approved' ORDER BY id")]}

    @app.post("/api/memory", status_code=201)
    async def create_fact(body: FactIn):
        now = clock()
        with store.connect() as db:
            for field, table in (("project_id", "projects"), ("listing_id", "listings"),
                                 ("inventory_item_id", "inventory_items")):
                if getattr(body, field) is not None:
                    require(db, table, getattr(body, field), "target_not_found")
            fact_id = db.execute("""INSERT INTO memory_facts(scope,project_id,listing_id,inventory_item_id,key,value,unit,
                source,origin,confidence,status,created,updated) VALUES(?,?,?,?,?,?,?,?,'user','stated','approved',?,?)""",
                (body.scope, body.project_id, body.listing_id, body.inventory_item_id, normalize_key(body.key),
                 body.value, body.unit, body.source, now, now)).lastrowid
            audit(db, "created", "memory_fact", fact_id, {"key": body.key, "scope": body.scope}, now)
            return dict(fact(db, fact_id))

    @app.get("/api/memory/{identifier}")
    async def get_fact(identifier: int):
        with store.connect() as db:
            return dict(fact(db, identifier))

    @app.patch("/api/memory/{identifier}")
    async def patch_fact(identifier: int, body: FactPatch):
        now = clock()
        with store.connect() as db:
            row = fact(db, identifier)
            check_version(row, body.version)
            if body.action == "approve":
                if row["status"] != "suggested":
                    raise ApiError(409, "not_suggested", "Only suggested facts can be approved")
                db.execute("UPDATE memory_facts SET status='approved',version=version+1,updated=? WHERE id=?", (now, identifier))
            elif body.action == "reject":
                if row["status"] not in ("suggested", "approved"):
                    raise ApiError(409, "not_active", "Only suggested or approved facts can be rejected")
                db.execute("UPDATE memory_facts SET status='rejected',version=version+1,updated=? WHERE id=?", (now, identifier))
            else:
                if row["status"] not in ("suggested", "approved"):
                    raise ApiError(409, "not_active", "Only suggested or approved facts can be corrected")
                new_id = db.execute("""INSERT INTO memory_facts(scope,project_id,listing_id,inventory_item_id,key,value,
                    unit,source,origin,confidence,status,supersedes_id,created,updated)
                    VALUES(?,?,?,?,?,?,?,'corrected by user','user','stated','approved',?,?,?)""",
                    (row["scope"], row["project_id"], row["listing_id"], row["inventory_item_id"], row["key"],
                     body.value, body.unit if body.unit is not None else row["unit"], identifier, now, now)).lastrowid
                db.execute("UPDATE memory_facts SET status='superseded',version=version+1,updated=? WHERE id=?",
                           (now, identifier))
                audit(db, "corrected", "memory_fact", identifier, {"replacement": new_id}, now)
                return dict(fact(db, new_id))
            audit(db, {"approve": "approved", "reject": "rejected"}[body.action], "memory_fact", identifier, {}, now)
            return dict(fact(db, identifier))

    @app.delete("/api/memory/{identifier}", status_code=204)
    async def delete_fact(identifier: int):
        with store.connect() as db:
            fact(db, identifier)
            db.execute("UPDATE memory_facts SET supersedes_id=NULL WHERE supersedes_id=?", (identifier,))
            db.execute("DELETE FROM memory_facts WHERE id=?", (identifier,))
            audit(db, "deleted", "memory_fact", identifier, {}, clock())

    @app.get("/api/audit")
    async def audit_log(entity: Key | None = None, entity_id: int | None = None, limit: int = Query(50, ge=1, le=200)):
        with store.connect() as db:
            return [dict(r, data=json.loads(r["data"])) for r in db.execute("""SELECT * FROM audit_events
                WHERE (? IS NULL OR entity=?) AND (? IS NULL OR entity_id=?) ORDER BY id DESC LIMIT ?""",
                (entity, entity, entity_id, entity_id, limit))]
