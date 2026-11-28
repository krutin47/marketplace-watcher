-- Gate B2: projects, inventory, purchases, approved memory and audit trail. Money is integer minor units.
CREATE TABLE projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','completed','archived')),
 target_date TEXT,
 budget_minor INTEGER CHECK(budget_minor IS NULL OR budget_minor >= 0),
 currency TEXT NOT NULL DEFAULT 'CAD',
 notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL,
 updated REAL NOT NULL
);

CREATE TABLE project_requirements (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 position INTEGER NOT NULL,
 label TEXT NOT NULL,
 attribute TEXT NOT NULL,
 operator TEXT NOT NULL CHECK(operator IN ('>=','<=','=','!=','contains','present')),
 value TEXT,
 unit TEXT,
 hardness TEXT NOT NULL CHECK(hardness IN ('hard','soft')),
 weight INTEGER NOT NULL DEFAULT 0 CHECK(weight BETWEEN 0 AND 100),
 evidence_required INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX requirement_project ON project_requirements(project_id, position);

CREATE TABLE budget_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 previous_minor INTEGER,
 new_minor INTEGER,
 reason TEXT NOT NULL,
 created REAL NOT NULL
);

CREATE TABLE project_listings (
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
 role TEXT NOT NULL DEFAULT 'candidate' CHECK(role IN ('candidate','shortlisted','rejected','purchased')),
 note TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL,
 PRIMARY KEY(project_id, listing_id)
);
CREATE INDEX project_listing_listing ON project_listings(listing_id);

CREATE TABLE inventory_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 category TEXT NOT NULL,
 manufacturer TEXT,
 model TEXT,
 quantity INTEGER NOT NULL CHECK(quantity >= 0),
 condition TEXT,
 lifecycle TEXT NOT NULL DEFAULT 'in_use' CHECK(lifecycle IN ('in_use','spare','retired','sold')),
 acquisition_cost_minor INTEGER CHECK(acquisition_cost_minor IS NULL OR acquisition_cost_minor >= 0),
 acquisition_currency TEXT,
 acquired_on TEXT,
 serial TEXT,
 attributes TEXT NOT NULL DEFAULT '{}',
 attributes_schema INTEGER NOT NULL DEFAULT 1,
 notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL,
 updated REAL NOT NULL
);
CREATE INDEX inventory_category ON inventory_items(category, model);

CREATE TABLE inventory_allocations (
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE RESTRICT,
 quantity INTEGER NOT NULL CHECK(quantity > 0),
 override_reason TEXT,
 created REAL NOT NULL,
 PRIMARY KEY(project_id, inventory_item_id)
);

CREATE TABLE purchases (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER REFERENCES listings(id) ON DELETE SET NULL,
 project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
 description TEXT NOT NULL,
 amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
 currency TEXT NOT NULL,
 purchased_at REAL NOT NULL,
 reason TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL
);
CREATE INDEX purchase_project ON purchases(project_id);

CREATE TABLE memory_facts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scope TEXT NOT NULL CHECK(scope IN ('global','project','inventory','listing','decision')),
 project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
 listing_id INTEGER REFERENCES listings(id) ON DELETE CASCADE,
 inventory_item_id INTEGER REFERENCES inventory_items(id) ON DELETE CASCADE,
 key TEXT NOT NULL,
 value TEXT NOT NULL,
 unit TEXT,
 source TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('user','assistant')),
 confidence TEXT NOT NULL DEFAULT 'stated',
 status TEXT NOT NULL CHECK(status IN ('suggested','approved','rejected','superseded')),
 supersedes_id INTEGER REFERENCES memory_facts(id) ON DELETE SET NULL,
 version INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL,
 updated REAL NOT NULL
);
CREATE INDEX memory_scope ON memory_facts(status, scope, project_id, listing_id);

CREATE TABLE audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 actor TEXT NOT NULL,
 action TEXT NOT NULL,
 entity TEXT NOT NULL,
 entity_id INTEGER,
 data TEXT NOT NULL DEFAULT '{}',
 created REAL NOT NULL
);
CREATE INDEX audit_entity ON audit_events(entity, entity_id, created);

ALTER TABLE conversations ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL;
