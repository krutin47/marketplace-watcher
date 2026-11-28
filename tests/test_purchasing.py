"""Gate B2: projects, budgets, inventory, purchases, memory and project-aware chat (PROJ, INV, section 15, T-08)."""
import unittest

from test_assistant import AssistantCase, ENCLOSURE, PROBOX
from watcher.purchasing import FactIn, approved_facts, suggest_fact

HOME_MEDIA = {"name": "Home Media Server", "description": "About 10TB usable, reliable and low power.",
              "budget": "1000", "currency": "CAD"}
REQUIREMENTS = [
    {"label": "Usable capacity", "attribute": "usable capacity", "operator": ">=", "value": "10", "unit": "TB",
     "hardness": "hard"},
    {"label": "Drive health known", "attribute": "smart_report", "operator": "present", "hardness": "hard",
     "evidence_required": True},
    {"label": "Low idle power", "attribute": "idle_power", "operator": "<=", "value": "40", "unit": "W",
     "hardness": "soft", "weight": 60},
]


class PurchasingCase(AssistantCase):
    def project(self, body=HOME_MEDIA):
        response = self.client.post("/api/projects", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


class ProjectTests(PurchasingCase, unittest.TestCase):
    def test_project_requirements_and_budget_rules(self):
        project = self.project()
        self.assertEqual(project["budget"], "1000.00")
        self.assertEqual(project["budget_summary"]["remaining"], "1000.00")
        bad = [
            [REQUIREMENTS[0] | {"weight": 10}],
            [REQUIREMENTS[2] | {"weight": 70}, REQUIREMENTS[2] | {"weight": 40}],
            [REQUIREMENTS[0] | {"value": "lots"}],
            [REQUIREMENTS[0] | {"value": None}],
        ]
        for requirements in bad:
            with self.subTest(requirements=requirements):
                response = self.client.put(f"/api/projects/{project['id']}/requirements",
                                           json={"version": project["version"], "requirements": requirements})
                self.assertEqual(response.status_code, 422)
        updated = self.client.put(f"/api/projects/{project['id']}/requirements",
                                  json={"version": project["version"], "requirements": REQUIREMENTS}).json()
        self.assertEqual([r["hardness"] for r in updated["requirements"]], ["hard", "hard", "soft"])
        self.assertEqual(updated["requirements"][0]["attribute"], "usable_capacity")
        self.assertEqual(updated["version"], project["version"] + 1)
        stale = self.client.patch(f"/api/projects/{project['id']}", json={"version": project["version"], "name": "X"})
        self.assertEqual(stale.json()["code"], "version_conflict")
        silent = self.client.patch(f"/api/projects/{project['id']}", json={"version": updated["version"], "budget": "2000"})
        self.assertEqual(silent.status_code, 409)
        self.assertEqual(silent.json()["code"], "budget_change_requires_reason")
        raised = self.client.post(f"/api/projects/{project['id']}/budget",
                                  json={"version": updated["version"], "budget": "1200", "reason": "Added a UPS"}).json()
        self.assertEqual(raised["budget"], "1200.00")
        self.assertEqual(raised["budget_changes"][0]["reason"], "Added a UPS")
        self.assertEqual(raised["budget_changes"][0]["previous"], "1000.00")
        audit = self.client.get("/api/audit", params={"entity": "project", "entity_id": project["id"]}).json()
        self.assertIn("budget_changed", [event["action"] for event in audit])

    def test_spend_purchases_and_archive(self):
        project = self.project()
        listing_id = self.import_listing(PROBOX)
        other = self.import_listing(ENCLOSURE)
        self.client.post(f"/api/projects/{project['id']}/listings", json={"listing_id": listing_id, "role": "shortlisted"})
        again = self.client.post(f"/api/projects/{project['id']}/listings", json={"listing_id": listing_id})
        self.assertEqual(again.json()["code"], "already_linked")
        detail = self.client.get(f"/api/projects/{project['id']}").json()
        self.assertEqual(detail["budget_summary"]["actual"], "0.00", "a shortlisted estimate is not spend")
        self.assertIsNone(detail["budget_summary"]["committed"])
        unconfirmed = self.client.post("/api/purchases", json={"description": "ProBox", "amount": "550", "currency": "CAD",
                                                               "purchased_at": "2026-01-02T10:00:00Z"})
        self.assertEqual(unconfirmed.status_code, 422)
        bought = self.client.post("/api/purchases", json={
            "confirm": True, "description": "ProBox", "amount": "550", "currency": "CAD",
            "purchased_at": "2026-01-02T10:00:00Z", "listing_id": listing_id, "project_id": project["id"]})
        self.assertEqual(bought.status_code, 201)
        self.client.post("/api/purchases", json={
            "confirm": True, "description": "Cable", "amount": "12.5", "currency": "USD",
            "purchased_at": "2026-01-03T10:00:00Z", "project_id": project["id"]})
        detail = self.client.get(f"/api/projects/{project['id']}").json()
        summary = detail["budget_summary"]
        self.assertEqual((summary["actual"], summary["remaining"]), ("550.00", "450.00"))
        self.assertEqual(summary["unconverted"], [{"currency": "USD", "amount": "12.50"}])
        self.assertEqual(detail["listings"][0]["role"], "purchased")
        self.assertEqual(self.client.get("/api/inventory").json(), [], "a purchase never creates inventory")
        archived = self.client.patch(f"/api/projects/{project['id']}",
                                     json={"version": detail["version"], "status": "archived"}).json()
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(self.client.get(f"/api/listings/{listing_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/listings/{other}/projects").json(), [])
        self.assertEqual(self.client.get(f"/api/listings/{listing_id}/projects").json()[0]["role"], "purchased")

    def test_inventory_privacy_and_allocation_limits(self):
        project = self.project()
        second = self.project({"name": "Backup box"})
        created = self.client.post("/api/inventory", json={
            "name": "WD Red 4TB", "category": "Hard drive", "manufacturer": "WD", "model": "WD40EFRX", "quantity": 4,
            "serial": "WD-SECRET-123", "attributes": {"interface": "SATA", "capacity_tb": 4},
            "acquisition_cost": "80", "acquisition_currency": "CAD"})
        self.assertEqual(created.status_code, 201, created.text)
        item = created.json()
        self.assertEqual(item["category"], "hard_drive")
        self.assertEqual(item["serial"], "(stored privately)")
        self.assertEqual(self.client.get(f"/api/inventory/{item['id']}", params={"reveal_serial": True}).json()["serial"],
                         "WD-SECRET-123")
        half = self.client.post("/api/inventory", json={"name": "x", "category": "y", "acquisition_cost": "1"})
        self.assertEqual(half.status_code, 422)
        url = f"/api/projects/{project['id']}/allocations/{item['id']}"
        self.assertEqual(self.client.put(url, json={"quantity": 3}).status_code, 200)
        over = self.client.put(f"/api/projects/{second['id']}/allocations/{item['id']}", json={"quantity": 2})
        self.assertEqual(over.json()["code"], "allocation_exceeds_quantity")
        forced = self.client.put(f"/api/projects/{second['id']}/allocations/{item['id']}",
                                 json={"quantity": 2, "override_reason": "One drive is arriving next week"})
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(forced.json()["allocations"][0]["override_reason"], "One drive is arriving next week")
        shrink = self.client.patch(f"/api/inventory/{item['id']}", json={"version": item["version"], "quantity": 1})
        self.assertEqual(shrink.json()["code"], "quantity_below_allocated")
        self.assertEqual(self.client.delete(f"/api/inventory/{item['id']}").json()["code"], "item_allocated")
        self.assertEqual(self.client.delete(url).status_code, 204)
        listed = self.client.get("/api/inventory").json()[0]
        self.assertEqual(listed["allocated"], 2)
        audit = self.client.get("/api/audit", params={"entity": "inventory_item"}).json()
        self.assertNotIn("WD-SECRET-123", str(audit))

    def test_memory_lifecycle(self):
        project = self.project()
        created = self.client.post("/api/memory", json={"scope": "project", "project_id": project["id"],
                                                        "key": "Max drive age", "value": "50000", "unit": "hours"})
        self.assertEqual(created.status_code, 201)
        fact = created.json()
        self.assertEqual((fact["status"], fact["origin"], fact["key"]), ("approved", "user", "max_drive_age"))
        self.assertEqual(self.client.post("/api/memory", json={"scope": "project", "key": "k", "value": "v"}).status_code, 422)
        with self.store.connect() as db:
            suggested = suggest_fact(db, FactIn(scope="global", key="prefers", value="quiet fans"), self.clock())
            self.assertEqual([f["id"] for f in approved_facts(db, project["id"], 1)], [fact["id"]])
        current = self.client.get(f"/api/memory/{suggested}").json()
        approved = self.client.patch(f"/api/memory/{suggested}", json={"version": current["version"], "action": "approve"})
        self.assertEqual(approved.json()["status"], "approved")
        corrected = self.client.patch(f"/api/memory/{fact['id']}", json={"version": fact["version"], "action": "correct",
                                                                         "value": "40000"}).json()
        self.assertEqual((corrected["value"], corrected["supersedes_id"]), ("40000", fact["id"]))
        self.assertEqual(self.client.get(f"/api/memory/{fact['id']}").json()["status"], "superseded")
        exported = self.client.get("/api/memory/export").json()["facts"]
        self.assertEqual(sorted(f["value"] for f in exported), ["40000", "quiet fans"])
        self.assertEqual(self.client.delete(f"/api/memory/{fact['id']}").status_code, 204)
        self.assertIsNone(self.client.get(f"/api/memory/{corrected['id']}").json()["supersedes_id"])


class ProjectChatTests(PurchasingCase, unittest.TestCase):
    def test_project_switch_and_memory_correction_in_chat(self):
        media = self.project()
        media = self.client.put(f"/api/projects/{media['id']}/requirements",
                                json={"version": media["version"], "requirements": REQUIREMENTS}).json()
        ai = self.project({"name": "Local AI Server", "budget": "300", "currency": "USD"})
        drive = self.client.post("/api/inventory", json={"name": "WD Red 4TB", "category": "hard_drive", "model": "WD40EFRX",
                                                         "quantity": 2, "serial": "SERIAL-PRIVATE-9"}).json()
        self.client.put(f"/api/projects/{media['id']}/allocations/{drive['id']}", json={"quantity": 2})
        media_fact = self.client.post("/api/memory", json={"scope": "project", "project_id": media["id"],
                                                           "key": "max_drive_hours", "value": "50000", "unit": "hours"}).json()
        self.client.post("/api/memory", json={"scope": "project", "project_id": ai["id"], "key": "min_vram",
                                              "value": "16", "unit": "GB"})
        with self.store.connect() as db:
            suggest_fact(db, FactIn(scope="global", key="unreviewed", value="assistant guess"), self.clock())
        listing_id = self.import_listing(PROBOX)
        chat = self.client.post("/api/conversations", json={"listing_id": listing_id, "project_id": media["id"]}).json()
        run_id, _ = self.ask(chat, listing_id, "Does this fit the project?")
        reply = self.reply(chat["id"])["content"]
        self.assertIn("Home Media Server", reply)
        self.assertIn("60% of the remaining budget", reply)
        self.assertIn("usable_capacity >= 10 TB", reply)
        self.assertIn("not yet checked automatically", reply)
        self.assertIn(f"[inventory:{drive['id']}]", reply)
        self.assertIn("max_drive_hours: 50000 hours", reply)
        self.assertNotIn("min_vram", reply)
        self.assertNotIn("assistant guess", reply)
        run = self.client.get(f"/api/assistant/runs/{run_id}").json()
        self.assertIn(f"project:{media['id']}@v{media['version'] }", {s["ref"] for s in run["context"]["sources"]})
        self.assertNotIn("SERIAL-PRIVATE-9", str(run))

        self.client.patch(f"/api/memory/{media_fact['id']}", json={"version": 1, "action": "correct", "value": "30000"})
        self.ask(chat, listing_id, "And now?")
        reply = self.reply(chat["id"])["content"]
        self.assertIn("max_drive_hours: 30000 hours", reply)
        self.assertNotIn("50000", reply)

        current = self.client.get(f"/api/conversations/{chat['id']}").json()
        mismatch = self.client.post(f"/api/conversations/{chat['id']}/messages", json={
            "content": "x", "request_key": self.key(), "listing_id": listing_id, "project_id": ai["id"]})
        self.assertEqual(mismatch.json()["code"], "scope_mismatch")
        self.client.patch(f"/api/conversations/{chat['id']}", json={"version": current["version"], "project_id": ai["id"]})
        self.ask(chat, listing_id, "What about for the AI server?")
        reply = self.reply(chat["id"])["content"]
        self.assertIn("Local AI Server", reply)
        self.assertIn("min_vram: 16 GB", reply)
        self.assertIn("listing currency differs from the budget currency", reply)
        self.assertNotIn("Home Media Server", reply)
        self.assertNotIn("max_drive_hours", reply)
        self.assertNotIn("WD Red", reply)

        current = self.client.get(f"/api/conversations/{chat['id']}").json()
        self.client.patch(f"/api/conversations/{chat['id']}", json={"version": current["version"],
                                                                   "excluded": ["project", "memory"]})
        self.ask(chat, listing_id, "Without project")
        reply = self.reply(chat["id"])["content"]
        self.assertNotIn("Local AI Server", reply)
        self.assertNotIn("min_vram", reply)
