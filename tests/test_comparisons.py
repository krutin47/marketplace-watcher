"""Gate C1: calculators (T-18) and comparison snapshots (CMP-01..04, T-07)."""
import unittest

from test_assistant import ENCLOSURE, PROBOX
from test_purchasing import HOME_MEDIA, REQUIREMENTS, PurchasingCase


class CalculatorTests(PurchasingCase, unittest.TestCase):
    def storage(self, drives, layout, **extra):
        return self.client.post("/api/calculations/storage", json={"drives_tb": drives, "layout": layout, **extra})

    def test_storage_layouts(self):
        cases = [([4, 4, 4, 4], "raid5", "12.00", "10.91"), ([4, 4, 4, 4], "raid6", "8.00", "7.28"),
                 ([4, 4, 4, 4], "mirror", "8.00", "7.28"), ([4, 4, 4, 4], "raid10", "8.00", "7.28"),
                 ([8, 8, 8, 8, 8, 8, 8, 8], "raid6", "48.00", "43.66"), ([4, 4, 8], "jbod", "16.00", "14.55"),
                 ([3, 3, 3], "mirror", "3.00", "2.73")]
        for drives, layout, usable, tib in cases:
            with self.subTest(drives=drives, layout=layout):
                extra = {"mirror_width": 3} if drives == [3, 3, 3] else {}
                result = self.storage(drives, layout, **extra)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual((result.json()["usable_tb"], result.json()["usable_tib"]), (usable, tib))
                self.assertTrue(any("backup" in a for a in result.json()["assumptions"]))
        mixed = self.storage([4, 4, 8], "raid5").json()
        self.assertEqual(mixed["usable_tb"], "8.00")
        self.assertTrue(any("4.00 TB unused" in a for a in mixed["assumptions"]))
        for drives, layout, extra in [([4, 4], "raid5", {}), ([4, 4, 4], "raid6", {}), ([4, 4, 4, 4, 4], "raid10", {}),
                                      ([4, 4, 4, 4], "mirror", {"mirror_width": 3}), ([4], "stripe", {}),
                                      ([0], "jbod", {}), ([], "jbod", {})]:
            with self.subTest(invalid=(drives, layout)):
                self.assertEqual(self.storage(drives, layout, **extra).status_code, 422)
        self.assertEqual(self.storage([4, 4, 4], "raid5").json()["fault_tolerance"], "Any one drive.")

    def test_energy_and_budget(self):
        energy = self.client.post("/api/calculations/energy", json={"watts": 50, "rate_per_kwh": "0.15", "currency": "CAD"}).json()
        self.assertEqual((energy["kwh_per_year"], energy["annual_cost"], energy["hours_per_year"]), ("438.00", "65.70", "8760"))
        self.assertEqual(energy["rate_basis"], "supplied by you")
        unpriced = self.client.post("/api/calculations/energy", json={"watts": 30, "hours_per_day": 8}).json()
        self.assertEqual((unpriced["kwh_per_year"], unpriced["annual_cost"]), ("87.60", None))
        self.assertIn("not supplied", unpriced["rate_basis"])
        self.assertEqual(self.client.post("/api/calculations/energy", json={"watts": 1, "rate_per_kwh": "0.1"}).status_code, 422)
        self.assertEqual(self.client.post("/api/calculations/energy", json={"watts": 1, "hours_per_day": 25}).status_code, 422)
        total = self.client.post("/api/calculations/budget", json={"budget": "1000", "currency": "CAD", "items": [
            {"label": "ProBox", "amount": "600", "currency": "CAD"}, {"label": "UPS", "amount": "450.50", "currency": "CAD"}]}).json()
        self.assertEqual((total["total"], total["remaining"], total["over_budget"]), ("1050.50", "-50.50", True))
        mixed = self.client.post("/api/calculations/budget", json={"budget": "1000", "currency": "CAD", "items": [
            {"label": "x", "amount": "1", "currency": "USD"}]})
        self.assertEqual(mixed.json()["code"], "invalid_calculation")


class ComparisonTests(PurchasingCase, unittest.TestCase):
    def setup_reference(self, requirements=REQUIREMENTS):
        project = self.project(HOME_MEDIA)
        self.client.put(f"/api/projects/{project['id']}/requirements",
                        json={"version": project["version"], "requirements": requirements})
        return project, self.import_listing(PROBOX), self.import_listing(ENCLOSURE)

    def test_reference_comparison_under_10tb_goal(self):
        project, probox, enclosure = self.setup_reference()
        created = self.client.post("/api/comparisons", json={"project_id": project["id"], "listing_ids": [probox, enclosure]})
        self.assertEqual(created.status_code, 201, created.text)
        snap = created.json()["snapshot"]
        self.assertEqual(snap["algorithm_version"], "comparison/1")
        a, b = snap["listings"]
        self.assertEqual((a["drives"]["count"], a["drives"]["size_tb"]), (8, "4"))
        self.assertEqual({c["layout"]: c["usable_tb"] for c in a["capacities"]},
                         {"raid6": "24.00", "raid5": "28.00", "mirror": "16.00", "raid10": "16.00"})
        capacity = {r["label"]: r for r in a["requirements"]}
        self.assertEqual(capacity["Usable capacity"]["result"], "meets")
        self.assertIn("calculated", capacity["Usable capacity"]["basis"])
        self.assertEqual(capacity["Drive health known"]["result"], "unknown")
        self.assertEqual(a["outcome"], "needs evidence")
        self.assertEqual(a["budget_share_percent"], "60")
        self.assertFalse(b["drives"]["stated"])
        self.assertIn("assume none", b["drives"]["note"])
        self.assertEqual(b["capacities"], [])
        self.assertEqual({r["label"]: r["result"] for r in b["requirements"]}["Usable capacity"], "unknown")
        self.assertEqual(b["budget_share_percent"], "6")
        self.assertIn("unknown", b["total_cost"]["basis"])
        self.assertIn("condition", b["unknown"])
        self.assertTrue(any("not purchase recommendations" in n for n in snap["notes"]))
        self.assertNotIn("buy", str([e["outcome"] for e in snap["listings"]]))

    def test_failed_hard_requirement_blocks(self):
        tight = [REQUIREMENTS[0], {"label": "Price cap", "attribute": "price", "operator": "<=", "value": "500",
                                   "unit": "CAD", "hardness": "hard"}]
        project, probox, enclosure = self.setup_reference(tight)
        snap = self.client.post("/api/comparisons", json={"project_id": project["id"], "listing_ids": [probox, enclosure]}).json()["snapshot"]
        self.assertEqual(snap["listings"][0]["outcome"], "blocked by a hard requirement")
        self.assertEqual(snap["listings"][1]["outcome"], "needs evidence")

    def test_validation_staleness_and_history(self):
        project, probox, enclosure = self.setup_reference()
        for ids in ([probox], [probox] * 2, [probox, enclosure, 3, 4, 5, 6]):
            with self.subTest(ids=ids):
                self.assertEqual(self.client.post("/api/comparisons", json={"listing_ids": ids}).status_code, 422)
        self.assertEqual(self.client.post("/api/comparisons", json={"listing_ids": [probox, 999]}).json()["code"], "listing_not_found")
        created = self.client.post("/api/comparisons", json={"listing_ids": [probox, enclosure]}).json()
        self.assertIsNone(created["snapshot"]["project"])
        self.assertFalse(created["stale"])
        first_snapshot = created["snapshot"]["id"]
        self.clock.now += 10
        self.client.post(f"/api/listings/{enclosure}/evidence", json={"kind": "seller_claim", "content": "Comes with 5x2TB drives"})
        current = self.client.get(f"/api/comparisons/{created['id']}").json()
        self.assertEqual(current["stale_reasons"], ["New seller updates or notes were added"])
        self.clock.now += 10
        patched = self.client.patch(f"/api/comparisons/{created['id']}",
                                    json={"version": current["version"], "project_id": project["id"]}).json()
        self.assertIn("The comparison's listings or project changed", patched["stale_reasons"])
        self.clock.now += 10
        refreshed = self.client.post(f"/api/comparisons/{created['id']}/refresh").json()
        self.assertFalse(refreshed["stale"])
        self.assertEqual(len(refreshed["history"]), 2)
        enclosure_now = refreshed["snapshot"]["listings"][1]
        self.assertEqual((enclosure_now["drives"]["count"], enclosure_now["drives"]["size_tb"]), (5, "2"))
        self.assertEqual({r["label"]: r["result"] for r in enclosure_now["requirements"]}["Usable capacity"], "fails")
        old = self.client.get(f"/api/comparisons/{created['id']}", params={"snapshot_id": first_snapshot}).json()
        self.assertFalse(old["snapshot"]["listings"][1]["drives"]["stated"])
        self.assertEqual(self.client.patch(f"/api/comparisons/{created['id']}", json={"version": 1}).json()["code"],
                         "version_conflict")
        self.assertEqual(self.client.delete(f"/api/comparisons/{created['id']}").status_code, 204)

    def test_chat_uses_calculator_not_guesses(self):
        probox = self.import_listing(PROBOX)
        chat = self.conversation(probox)
        run_id, _ = self.ask(chat, probox, "How much space would I get?")
        reply = self.reply(chat["id"])["content"]
        self.assertIn("8 × 4 TB", reply)
        self.assertIn("raid6 24.00 TB (21.83 TiB)", reply)
        self.assertIn("[calc:storage]", reply)
        enclosure = self.import_listing(ENCLOSURE)
        chat = self.conversation(enclosure)
        self.ask(chat, enclosure, "How much space?")
        reply = self.reply(chat["id"])["content"]
        self.assertIn("assuming the enclosure comes empty", reply)
        self.assertNotIn("calc:storage", reply)
