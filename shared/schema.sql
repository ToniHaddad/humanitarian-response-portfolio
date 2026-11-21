-- Shared schema for all twenty projects.
-- One SQLite database, one set of identifiers. Every table is created here and
-- nowhere else. Timestamps are ISO 8601 local time text ("YYYY-MM-DDTHH:MM:SS"),
-- dates are "YYYY-MM-DD". Map positions are kilometres on an invented planar grid
-- (x_km, y_km from 0 to 100) that does not correspond to any real place.
--
-- Rule against double counting: each real activity is stored once.
--   * A call is a row in calls. A complaint is a row in complaints. Repeat calls
--     about a complaint add calls, never complaints.
--   * Stock changes live only in stock_movements. On-hand is derived (view).
--   * Parcels handed to households live only in distributions.
--   * Parcels and ready-to-eat meals are separate columns, never one quantity.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

-- Fictional areas on the invented grid. Used by every project.
CREATE TABLE areas (
    area_id      TEXT PRIMARY KEY,          -- e.g. AR-01
    name_en      TEXT NOT NULL,
    name_ar      TEXT NOT NULL,
    district     TEXT NOT NULL,
    x_km         REAL NOT NULL,             -- centroid on the invented grid
    y_km         REAL NOT NULL,
    population_estimate INTEGER NOT NULL
);

-- Distribution points, community centres and clinics.
CREATE TABLE sites (
    site_id      TEXT PRIMARY KEY,          -- e.g. SITE-01
    name         TEXT NOT NULL,
    name_ar      TEXT NOT NULL,
    area_id      TEXT NOT NULL REFERENCES areas(area_id),
    site_type    TEXT NOT NULL CHECK (site_type IN ('distribution_point','community_centre','clinic')),
    x_km         REAL NOT NULL,
    y_km         REAL NOT NULL
);

CREATE TABLE warehouses (
    warehouse_id TEXT PRIMARY KEY,          -- e.g. WH-01
    name         TEXT NOT NULL,
    area_id      TEXT NOT NULL REFERENCES areas(area_id),
    x_km         REAL NOT NULL,
    y_km         REAL NOT NULL
);

-- Relief items. unit says how the item is counted (parcel, meal, kit, ...).
CREATE TABLE items (
    item_id      TEXT PRIMARY KEY,          -- e.g. ITM-FP-STD
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,             -- food_parcel, rte_meal, hygiene, shelter, water, medical
    unit         TEXT NOT NULL,             -- parcel, meal, kit, litre, box
    unit_weight_kg REAL NOT NULL,
    -- For food parcels: person-days of food in one parcel (e.g. 5 people x 30 days = 150).
    person_days_per_unit REAL
);

-- Staff are identified by role IDs only. No real or realistic personal names.
CREATE TABLE staff (
    staff_id     TEXT PRIMARY KEY,          -- e.g. OPR-01
    display_name TEXT NOT NULL,             -- e.g. "Operator 01"
    role         TEXT NOT NULL CHECK (role IN ('operator','caseworker','coordinator','duty_officer','warehouse_officer','field_worker','admin')),
    active       INTEGER NOT NULL DEFAULT 1
);

-- Demo settings and editable limits (stale-position limit, callback SLA, ...).
CREATE TABLE settings (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    description  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    updated_by   TEXT
);

-- ---------------------------------------------------------------------------
-- Households (fictional) and their history of merges (Project 06)
-- ---------------------------------------------------------------------------

CREATE TABLE households (
    household_id     TEXT PRIMARY KEY,      -- e.g. HH-00042
    head_name        TEXT NOT NULL,         -- fictional, as entered (Arabic or Latin script)
    name_script      TEXT NOT NULL CHECK (name_script IN ('ar','latin')),
    head_birth_year  INTEGER,
    head_gender      TEXT CHECK (head_gender IN ('F','M')),
    household_size   INTEGER NOT NULL CHECK (household_size > 0),
    area_id          TEXT NOT NULL REFERENCES areas(area_id),
    registered_at    TEXT NOT NULL,
    registered_by    TEXT REFERENCES staff(staff_id),
    vulnerability    TEXT,                  -- comma list: elderly_head, disability, female_headed, chronic_illness
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','merged','inactive')),
    merged_into      TEXT REFERENCES households(household_id)
);

-- Every merge decision, including "not a duplicate". A merge never deletes a row:
-- the merged household keeps its row with status 'merged' and a full snapshot here.
CREATE TABLE merge_history (
    merge_id            TEXT PRIMARY KEY,   -- e.g. MRG-0001
    kept_household_id   TEXT NOT NULL REFERENCES households(household_id),
    other_household_id  TEXT NOT NULL REFERENCES households(household_id),
    decision            TEXT NOT NULL CHECK (decision IN ('merged','not_duplicate')),
    match_score         REAL,
    kept_snapshot_json  TEXT NOT NULL,
    other_snapshot_json TEXT NOT NULL,
    field_choices_json  TEXT,               -- which value was kept for each conflicting field
    moved_records_json  TEXT,               -- child rows re-pointed to the kept household
    reason              TEXT NOT NULL,
    decided_by          TEXT NOT NULL REFERENCES staff(staff_id),
    decided_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Call centre and casework (Projects 01, 02, 03)
-- ---------------------------------------------------------------------------

-- Service requests. category is the aid type (food, shelter, health, cash,
-- water_hygiene, protection). suggested_* comes from the Project 03 classifier.
CREATE TABLE service_requests (
    request_id           TEXT PRIMARY KEY,  -- e.g. REQ-00001
    created_at           TEXT NOT NULL,
    household_id         TEXT REFERENCES households(household_id),
    area_id              TEXT NOT NULL REFERENCES areas(area_id),
    notes                TEXT NOT NULL,
    language             TEXT NOT NULL CHECK (language IN ('ar','en')),
    category             TEXT,              -- confirmed by the operator; NULL until confirmed
    suggested_category   TEXT,              -- NULL when the model is not confident
    suggested_confidence REAL,
    status               TEXT NOT NULL CHECK (status IN ('new','assigned','callback_scheduled','in_progress','closed')),
    priority             TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    assigned_to          TEXT REFERENCES staff(staff_id),
    assigned_at          TEXT,
    callback_due_at      TEXT,
    callback_done_at     TEXT,
    closed_at            TEXT,
    closure_outcome      TEXT CHECK (closure_outcome IN ('assisted','referred','not_eligible','unreachable','duplicate','withdrawn')),
    created_by           TEXT REFERENCES staff(staff_id)
);

-- Timeline of a request: created, assigned, callback_scheduled, callback_done, note, closed.
CREATE TABLE request_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL REFERENCES service_requests(request_id),
    at          TEXT NOT NULL,
    staff_id    TEXT REFERENCES staff(staff_id),
    event_type  TEXT NOT NULL CHECK (event_type IN ('created','category_confirmed','assigned','callback_scheduled','callback_done','note','closed','reopened')),
    detail      TEXT
);

CREATE TABLE complaints (
    complaint_id      TEXT PRIMARY KEY,     -- e.g. CMP-0001
    opened_at         TEXT NOT NULL,
    household_id      TEXT REFERENCES households(household_id),
    area_id           TEXT NOT NULL REFERENCES areas(area_id),
    topic             TEXT NOT NULL,        -- distribution_quality, waiting_time, staff_conduct, exclusion, parcel_contents, information, other
    description       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('open','in_progress','closed')),
    owner_id          TEXT REFERENCES staff(staff_id),
    closed_at         TEXT,
    resolution        TEXT,
    created_by        TEXT REFERENCES staff(staff_id)
);

CREATE TABLE complaint_followups (
    followup_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id  TEXT NOT NULL REFERENCES complaints(complaint_id),
    at            TEXT NOT NULL,
    staff_id      TEXT NOT NULL REFERENCES staff(staff_id),
    action        TEXT NOT NULL CHECK (action IN ('owner_assigned','contacted_family','escalated','site_visit','note','closed','reopened')),
    note          TEXT NOT NULL
);

-- Every incoming call (Project 02). A call may link to one complaint and/or one
-- service request. Many calls may link to the same complaint.
CREATE TABLE calls (
    call_id        TEXT PRIMARY KEY,        -- e.g. CALL-000001
    received_at    TEXT NOT NULL,
    operator_id    TEXT NOT NULL REFERENCES staff(staff_id),
    household_id   TEXT REFERENCES households(household_id),
    area_id        TEXT REFERENCES areas(area_id),
    call_type      TEXT NOT NULL CHECK (call_type IN ('information','service_request','complaint','follow_up')),
    language       TEXT NOT NULL CHECK (language IN ('ar','en')),
    notes          TEXT NOT NULL,
    duration_sec   INTEGER,
    complaint_id   TEXT REFERENCES complaints(complaint_id),
    request_id     TEXT REFERENCES service_requests(request_id),
    is_repeat      INTEGER NOT NULL DEFAULT 0  -- 1 when the call is about an already logged complaint/request
);

-- Synthetic labelled call notes used to train and evaluate the classifier (Project 03).
CREATE TABLE classifier_notes (
    note_id   INTEGER PRIMARY KEY,
    text      TEXT NOT NULL,
    language  TEXT NOT NULL CHECK (language IN ('ar','en')),
    label     TEXT NOT NULL,
    split     TEXT NOT NULL CHECK (split IN ('train','test'))
);

-- ---------------------------------------------------------------------------
-- Referrals (Project 04)
-- ---------------------------------------------------------------------------

CREATE TABLE service_providers (
    provider_id   TEXT PRIMARY KEY,         -- e.g. PRV-01 (fictional)
    name          TEXT NOT NULL,
    name_ar       TEXT NOT NULL,
    area_id       TEXT NOT NULL REFERENCES areas(area_id),
    x_km          REAL NOT NULL,
    y_km          REAL NOT NULL,
    open_days     TEXT NOT NULL,            -- e.g. "Mon-Fri"
    languages     TEXT NOT NULL             -- e.g. "ar,en"
);

CREATE TABLE provider_services (
    provider_id       TEXT NOT NULL REFERENCES service_providers(provider_id),
    service_category  TEXT NOT NULL,        -- same vocabulary as service_requests.category
    service_name      TEXT NOT NULL,
    keywords          TEXT NOT NULL,        -- comma list used for matching, English and Arabic
    weekly_capacity   INTEGER NOT NULL,
    current_load      INTEGER NOT NULL,
    eligibility       TEXT NOT NULL,        -- plain-language rule shown to the caseworker
    accepts_new       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (provider_id, service_category, service_name)
);

CREATE TABLE referrals (
    referral_id          TEXT PRIMARY KEY,  -- e.g. REF-0001
    created_at           TEXT NOT NULL,
    household_id         TEXT NOT NULL REFERENCES households(household_id),
    request_id           TEXT REFERENCES service_requests(request_id),
    need_text            TEXT NOT NULL,
    service_category     TEXT NOT NULL,
    provider_id          TEXT NOT NULL REFERENCES service_providers(provider_id),
    service_name         TEXT NOT NULL,
    match_score          REAL,
    match_explanation    TEXT,
    status               TEXT NOT NULL CHECK (status IN ('proposed','approved','accepted','declined','support_received','support_not_received')),
    caseworker_id        TEXT NOT NULL REFERENCES staff(staff_id),
    approved_by          TEXT REFERENCES staff(staff_id),
    approved_at          TEXT,
    provider_response    TEXT CHECK (provider_response IN ('accepted','declined')),
    provider_response_note TEXT,
    provider_response_at TEXT,
    family_received      INTEGER,           -- 1 yes, 0 no, NULL not yet confirmed
    outcome_note         TEXT,
    outcome_confirmed_at TEXT,
    outcome_confirmed_by TEXT REFERENCES staff(staff_id)
);

-- ---------------------------------------------------------------------------
-- Data intake, pipeline and offline sync (Projects 05, 07, 08)
-- ---------------------------------------------------------------------------

-- Intake records created from field notes (05) or field devices (08).
-- version increases by one on every saved change; record_versions keeps each one.
CREATE TABLE intake_records (
    record_id       TEXT PRIMARY KEY,       -- e.g. INT-0001
    source          TEXT NOT NULL CHECK (source IN ('data_entry','field_device','server')),
    household_id    TEXT REFERENCES households(household_id),
    head_name       TEXT NOT NULL,
    area_id         TEXT REFERENCES areas(area_id),
    household_size  INTEGER,
    primary_need    TEXT,
    urgency         TEXT CHECK (urgency IN ('low','normal','high','urgent')),
    notes           TEXT,
    raw_note        TEXT,                   -- the original pasted note (05)
    language        TEXT CHECK (language IN ('ar','en')),
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL REFERENCES staff(staff_id),
    updated_at      TEXT NOT NULL,
    updated_by      TEXT NOT NULL REFERENCES staff(staff_id)
);

CREATE TABLE record_versions (
    record_id    TEXT NOT NULL REFERENCES intake_records(record_id),
    version      INTEGER NOT NULL,
    data_json    TEXT NOT NULL,
    origin       TEXT NOT NULL CHECK (origin IN ('server','device','conflict_resolution','data_entry')),
    device_id    TEXT,
    saved_at     TEXT NOT NULL,
    saved_by     TEXT NOT NULL REFERENCES staff(staff_id),
    PRIMARY KEY (record_id, version)
);

-- Drafts extracted from pasted notes before staff save them (05).
CREATE TABLE intake_drafts (
    draft_id        TEXT PRIMARY KEY,       -- e.g. DRF-0001
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL REFERENCES staff(staff_id),
    raw_note        TEXT NOT NULL,
    language        TEXT NOT NULL CHECK (language IN ('ar','en')),
    extracted_json  TEXT NOT NULL,
    flags_json      TEXT NOT NULL,          -- missing / uncertain fields
    status          TEXT NOT NULL CHECK (status IN ('draft','saved','discarded')),
    saved_record_id TEXT REFERENCES intake_records(record_id)
);

-- Field devices that capture offline (08).
CREATE TABLE sync_devices (
    device_id      TEXT PRIMARY KEY,        -- e.g. DEV-01
    assigned_to    TEXT NOT NULL REFERENCES staff(staff_id),
    online         INTEGER NOT NULL DEFAULT 1,
    last_sync_at   TEXT
);

-- Local outgoing queue on each device. base_version is the server version the
-- device edited from; a mismatch at sync time is a conflict.
CREATE TABLE sync_queue (
    queue_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL REFERENCES sync_devices(device_id),
    record_id     TEXT NOT NULL,
    operation     TEXT NOT NULL CHECK (operation IN ('create','update')),
    payload_json  TEXT NOT NULL,
    base_version  INTEGER,
    queued_at     TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','synced','conflict','resolved')),
    synced_at     TEXT,
    conflict_id   TEXT REFERENCES sync_conflicts(conflict_id)
);

CREATE TABLE sync_conflicts (
    conflict_id         TEXT PRIMARY KEY,   -- e.g. CNF-0001
    record_id           TEXT NOT NULL REFERENCES intake_records(record_id),
    device_id           TEXT NOT NULL REFERENCES sync_devices(device_id),
    base_version        INTEGER NOT NULL,
    server_version      INTEGER NOT NULL,
    device_data_json    TEXT NOT NULL,
    server_data_json    TEXT NOT NULL,
    detected_at         TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('open','resolved')),
    resolution          TEXT CHECK (resolution IN ('keep_device','keep_server','field_by_field')),
    resolved_data_json  TEXT,
    resolved_by         TEXT REFERENCES staff(staff_id),
    resolved_at         TEXT,
    reason              TEXT
);

-- Field teams that send spreadsheet exports (07).
CREATE TABLE pipeline_sources (
    source_id    TEXT PRIMARY KEY,          -- e.g. TEAM-NORTH
    description  TEXT NOT NULL,
    file_pattern TEXT NOT NULL
);

CREATE TABLE pipeline_runs (
    run_id            TEXT PRIMARY KEY,     -- e.g. RUN-0001
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    trigger           TEXT NOT NULL CHECK (trigger IN ('scheduled','manual','api')),
    source_id         TEXT REFERENCES pipeline_sources(source_id),
    file_name         TEXT NOT NULL,
    file_sha256       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('running','success','partial','failed')),
    rows_read         INTEGER NOT NULL DEFAULT 0,
    rows_loaded       INTEGER NOT NULL DEFAULT 0,
    rows_duplicate    INTEGER NOT NULL DEFAULT 0,
    rows_quarantined  INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT
);

CREATE TABLE pipeline_quarantine (
    quarantine_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    source_id      TEXT REFERENCES pipeline_sources(source_id),
    row_number     INTEGER NOT NULL,
    raw_row_json   TEXT NOT NULL,
    rule           TEXT NOT NULL,           -- e.g. missing_household_id, bad_date, unknown_area
    reason         TEXT NOT NULL
);

-- Rows loaded by the pipeline. row_key is a hash of the natural key, UNIQUE, so
-- loading the same file twice cannot create duplicates.
CREATE TABLE field_assessments (
    assessment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    row_key         TEXT NOT NULL UNIQUE,
    source_id       TEXT NOT NULL REFERENCES pipeline_sources(source_id),
    household_id    TEXT NOT NULL REFERENCES households(household_id),
    area_id         TEXT NOT NULL REFERENCES areas(area_id),
    assessment_date TEXT NOT NULL,
    household_size  INTEGER NOT NULL,
    need_category   TEXT NOT NULL,
    priority        TEXT NOT NULL,
    loaded_run_id   TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    loaded_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Supply chain and distributions (Projects 09, 10, 11, 12)
-- ---------------------------------------------------------------------------

CREATE TABLE item_lead_times (
    warehouse_id    TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    item_id         TEXT NOT NULL REFERENCES items(item_id),
    lead_time_days  INTEGER NOT NULL,
    safety_days     INTEGER NOT NULL,
    reorder_threshold INTEGER NOT NULL,     -- alert rule default: stock below this
    PRIMARY KEY (warehouse_id, item_id)
);

-- Planned distributions (11 feeds 10's forecast; 12 reconciles against them).
CREATE TABLE distribution_plans (
    plan_id           TEXT PRIMARY KEY,     -- e.g. PLN-0001
    site_id           TEXT NOT NULL REFERENCES sites(site_id),
    warehouse_id      TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    item_id           TEXT NOT NULL REFERENCES items(item_id),
    planned_date      TEXT NOT NULL,
    planned_quantity  INTEGER NOT NULL CHECK (planned_quantity >= 0),
    planned_households INTEGER,
    status            TEXT NOT NULL CHECK (status IN ('planned','dispatched','completed','cancelled')),
    created_by        TEXT REFERENCES staff(staff_id)
);

-- Convoys (16). Declared before dispatches because dispatches reference them.
CREATE TABLE convoys (
    convoy_id             TEXT PRIMARY KEY, -- e.g. CNV-001
    name                  TEXT NOT NULL,
    planned_date          TEXT NOT NULL,
    origin_warehouse_id   TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    status                TEXT NOT NULL CHECK (status IN ('draft','planned','approved','departed','arrived','returned','cancelled')),
    coordinator_id        TEXT NOT NULL REFERENCES staff(staff_id),
    manual_order_json     TEXT,             -- coordinator's stop order (list of site_id)
    suggested_order_json  TEXT,             -- planner's stop order
    manual_distance_km    REAL,
    suggested_distance_km REAL,
    approved_by           TEXT REFERENCES staff(staff_id),
    approved_at           TEXT,
    departed_at           TEXT,
    arrived_at            TEXT,
    returned_at           TEXT,
    notes                 TEXT
);

-- A dispatch is stock leaving a warehouse for a site. Its quantity is recorded
-- once more in stock_movements (movement_type 'dispatched', dispatch_id set),
-- which is the only table on-hand stock is computed from.
CREATE TABLE dispatches (
    dispatch_id    TEXT PRIMARY KEY,        -- e.g. DSP-0001
    plan_id        TEXT REFERENCES distribution_plans(plan_id),
    convoy_id      TEXT REFERENCES convoys(convoy_id),
    warehouse_id   TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    item_id        TEXT NOT NULL REFERENCES items(item_id),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    dispatched_at  TEXT NOT NULL,
    dispatched_by  TEXT REFERENCES staff(staff_id)
);

-- The stock ledger. on hand = sum(received) - sum(dispatched), per warehouse and item.
-- Returns from sites come back as 'received' with reason 'distribution_return'.
-- Write-offs leave as 'dispatched' with reason 'write_off'.
CREATE TABLE stock_movements (
    movement_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id   TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    item_id        TEXT NOT NULL REFERENCES items(item_id),
    moved_at       TEXT NOT NULL,
    movement_type  TEXT NOT NULL CHECK (movement_type IN ('received','dispatched')),
    reason         TEXT NOT NULL CHECK (reason IN ('supplier_delivery','transfer_in','distribution_return','dispatch','transfer_out','write_off')),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    dispatch_id    TEXT REFERENCES dispatches(dispatch_id),
    reference      TEXT,
    recorded_by    TEXT REFERENCES staff(staff_id)
);

-- Parcels and meals handed to households (11). Parcels and ready-to-eat meals are
-- separate columns in separate units and are never added together.
CREATE TABLE distributions (
    distribution_id        TEXT PRIMARY KEY, -- e.g. DST-00001
    distribution_date      TEXT NOT NULL,
    site_id                TEXT NOT NULL REFERENCES sites(site_id),
    household_id           TEXT NOT NULL REFERENCES households(household_id),
    household_size         INTEGER NOT NULL CHECK (household_size > 0),
    parcel_item_id         TEXT REFERENCES items(item_id),
    parcel_quantity        INTEGER NOT NULL DEFAULT 0 CHECK (parcel_quantity >= 0),   -- unit: parcels
    rte_meal_quantity      INTEGER NOT NULL DEFAULT 0 CHECK (rte_meal_quantity >= 0), -- unit: meals
    expected_coverage_days INTEGER NOT NULL,
    dispatch_id            TEXT REFERENCES dispatches(dispatch_id),
    request_id             TEXT REFERENCES service_requests(request_id),
    recorded_by            TEXT REFERENCES staff(staff_id),
    recorded_at            TEXT NOT NULL
);

-- Parcels returned from a site after a distribution day (12).
CREATE TABLE distribution_returns (
    return_id     TEXT PRIMARY KEY,         -- e.g. RET-0001
    dispatch_id   TEXT REFERENCES dispatches(dispatch_id),
    site_id       TEXT NOT NULL REFERENCES sites(site_id),
    item_id       TEXT NOT NULL REFERENCES items(item_id),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    returned_at   TEXT NOT NULL,
    recorded_by   TEXT REFERENCES staff(staff_id)
);

-- Editable forecast assumptions (11) and other model parameters.
CREATE TABLE forecast_assumptions (
    assumption_key TEXT PRIMARY KEY,
    value          REAL NOT NULL,
    unit           TEXT NOT NULL,
    description    TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    updated_by     TEXT REFERENCES staff(staff_id)
);

-- Staff notes on each reconciliation discrepancy (12).
CREATE TABLE reconciliation_notes (
    note_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id           TEXT NOT NULL REFERENCES sites(site_id),
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    discrepancy_key   TEXT NOT NULL,        -- e.g. "duplicate:HH-00042" or "unmatched:DST-00310"
    discrepancy_type  TEXT NOT NULL CHECK (discrepancy_type IN ('unaccounted','duplicate_household','unmatched_dispatch','other')),
    note              TEXT NOT NULL,
    resolved          INTEGER NOT NULL DEFAULT 0,
    staff_id          TEXT NOT NULL REFERENCES staff(staff_id),
    at                TEXT NOT NULL
);

CREATE TABLE reconciliation_reports (
    report_id     TEXT PRIMARY KEY,         -- e.g. RCN-0001
    site_id       TEXT NOT NULL REFERENCES sites(site_id),
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    summary_json  TEXT NOT NULL,
    markdown      TEXT NOT NULL,
    approved_by   TEXT NOT NULL REFERENCES staff(staff_id),
    approved_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Volunteers (Project 13)
-- ---------------------------------------------------------------------------

CREATE TABLE volunteers (
    volunteer_id  TEXT PRIMARY KEY,         -- e.g. VOL-001
    home_area_id  TEXT NOT NULL REFERENCES areas(area_id),
    languages     TEXT NOT NULL,
    max_shifts_per_week INTEGER NOT NULL DEFAULT 3,
    active        INTEGER NOT NULL DEFAULT 1
);

-- training: driving, packing, registration, distribution, first_aid, safeguarding
CREATE TABLE volunteer_trainings (
    volunteer_id  TEXT NOT NULL REFERENCES volunteers(volunteer_id),
    training      TEXT NOT NULL,
    completed_on  TEXT NOT NULL,
    PRIMARY KEY (volunteer_id, training)
);

CREATE TABLE volunteer_availability (
    volunteer_id  TEXT NOT NULL REFERENCES volunteers(volunteer_id),
    avail_date    TEXT NOT NULL,
    start_time    TEXT NOT NULL,            -- HH:MM
    end_time      TEXT NOT NULL,
    PRIMARY KEY (volunteer_id, avail_date, start_time)
);

CREATE TABLE shifts (
    shift_id    TEXT PRIMARY KEY,           -- e.g. SHF-0001
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    shift_date  TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    purpose     TEXT NOT NULL
);

-- Roles needed per shift. Interpreter roles are excluded by design.
CREATE TABLE shift_roles (
    shift_id           TEXT NOT NULL REFERENCES shifts(shift_id),
    role               TEXT NOT NULL CHECK (role IN ('driver','packing','registration','distribution')),
    required_count     INTEGER NOT NULL CHECK (required_count > 0),
    required_training  TEXT NOT NULL,
    PRIMARY KEY (shift_id, role)
);

CREATE TABLE shift_assignments (
    assignment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id       TEXT NOT NULL REFERENCES shifts(shift_id),
    role           TEXT NOT NULL,
    volunteer_id   TEXT NOT NULL REFERENCES volunteers(volunteer_id),
    status         TEXT NOT NULL CHECK (status IN ('suggested','confirmed','cancelled')),
    assigned_by    TEXT REFERENCES staff(staff_id),
    assigned_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Fleet (Project 14) and convoys (Project 16)
-- ---------------------------------------------------------------------------

CREATE TABLE vehicles (
    vehicle_id             TEXT PRIMARY KEY, -- e.g. VEH-01 (fictional fleet number, not a plate)
    vehicle_type           TEXT NOT NULL CHECK (vehicle_type IN ('truck','van','pickup','4x4')),
    load_capacity_kg       REAL NOT NULL,
    odometer_km            INTEGER NOT NULL,
    last_service_date      TEXT NOT NULL,
    last_service_odometer  INTEGER NOT NULL,
    service_interval_km    INTEGER NOT NULL,
    service_interval_days  INTEGER NOT NULL,
    registration_expiry    TEXT NOT NULL,
    insurance_expiry       TEXT NOT NULL,
    permit_expiry          TEXT NOT NULL,
    home_warehouse_id      TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    simulated              INTEGER NOT NULL DEFAULT 1  -- included in the position simulator
);

CREATE TABLE vehicle_defects (
    defect_id     TEXT PRIMARY KEY,         -- e.g. DEF-001
    vehicle_id    TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    reported_at   TEXT NOT NULL,
    reported_by   TEXT REFERENCES staff(staff_id),
    description   TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('minor','major','grounding')),
    resolved_at   TEXT
);

CREATE TABLE drivers (
    driver_id      TEXT PRIMARY KEY,        -- e.g. DRV-01
    volunteer_id   TEXT REFERENCES volunteers(volunteer_id),
    licence_class  TEXT NOT NULL CHECK (licence_class IN ('B','C')),  -- C allows trucks
    licence_expiry TEXT NOT NULL,
    home_area_id   TEXT NOT NULL REFERENCES areas(area_id),
    active         INTEGER NOT NULL DEFAULT 1
);

-- Position reports, received through the Project 20 API (or written by the simulator
-- through the same function). One row per report.
CREATE TABLE position_reports (
    report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    convoy_id    TEXT REFERENCES convoys(convoy_id),
    reported_at  TEXT NOT NULL,
    x_km         REAL NOT NULL,
    y_km         REAL NOT NULL,
    speed_kmh    REAL,
    source       TEXT NOT NULL CHECK (source IN ('simulator','api','seed'))
);

CREATE TABLE convoy_vehicles (
    convoy_id   TEXT NOT NULL REFERENCES convoys(convoy_id),
    vehicle_id  TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    driver_id   TEXT NOT NULL REFERENCES drivers(driver_id),
    PRIMARY KEY (convoy_id, vehicle_id)
);

CREATE TABLE convoy_stops (
    convoy_id   TEXT NOT NULL REFERENCES convoys(convoy_id),
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    vehicle_id  TEXT REFERENCES vehicles(vehicle_id),
    stop_seq    INTEGER NOT NULL,
    PRIMARY KEY (convoy_id, site_id)
);

CREATE TABLE convoy_loads (
    load_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    convoy_id   TEXT NOT NULL REFERENCES convoys(convoy_id),
    vehicle_id  TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    item_id     TEXT NOT NULL REFERENCES items(item_id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    weight_kg   REAL NOT NULL
);

CREATE TABLE convoy_checklist (
    convoy_id   TEXT NOT NULL REFERENCES convoys(convoy_id),
    item_key    TEXT NOT NULL,
    label       TEXT NOT NULL,
    checked     INTEGER NOT NULL DEFAULT 0,
    checked_by  TEXT REFERENCES staff(staff_id),
    checked_at  TEXT,
    PRIMARY KEY (convoy_id, item_key)
);

-- Status changes per convoy: the trip log together with position_reports.
CREATE TABLE convoy_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    convoy_id   TEXT NOT NULL REFERENCES convoys(convoy_id),
    at          TEXT NOT NULL,
    status      TEXT NOT NULL,
    staff_id    TEXT REFERENCES staff(staff_id),
    note        TEXT
);

-- ---------------------------------------------------------------------------
-- Security (Project 15)
-- ---------------------------------------------------------------------------

-- Fictional incident reports. Unverified reports never feed the advisory rule.
CREATE TABLE incidents (
    incident_id          TEXT PRIMARY KEY,  -- e.g. INC-0001
    reported_at          TEXT NOT NULL,
    occurred_at          TEXT NOT NULL,
    area_id              TEXT NOT NULL REFERENCES areas(area_id),
    incident_type        TEXT NOT NULL,     -- road_closure, armed_clash, unrest, shelling, checkpoint_delay, crime
    severity             INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    source               TEXT NOT NULL CHECK (source IN ('field_team','community_contact','media','hotline','other_agency')),
    verification_status  TEXT NOT NULL CHECK (verification_status IN ('unverified','verified','discounted')),
    verified_by          TEXT REFERENCES staff(staff_id),
    verified_at          TEXT,
    description          TEXT NOT NULL,
    reported_by          TEXT REFERENCES staff(staff_id)
);

-- Advisory decisions per area. Level: 0 normal, 1 caution, 2 restricted, 3 no movement.
-- The newest row per area with active = 1 is the current advisory. Levels >= 2 block
-- convoy stops in that area (16).
CREATE TABLE advisories (
    advisory_id      TEXT PRIMARY KEY,      -- e.g. ADV-0001
    area_id          TEXT NOT NULL REFERENCES areas(area_id),
    suggested_level  INTEGER NOT NULL CHECK (suggested_level BETWEEN 0 AND 3),
    final_level      INTEGER NOT NULL CHECK (final_level BETWEEN 0 AND 3),
    is_override      INTEGER NOT NULL DEFAULT 0,
    override_reason  TEXT,
    rule_basis_json  TEXT NOT NULL,         -- incidents and scores the rule used
    decided_by       TEXT NOT NULL REFERENCES staff(staff_id),
    decided_at       TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    CHECK (is_override = 0 OR (override_reason IS NOT NULL AND length(trim(override_reason)) > 0))
);

-- ---------------------------------------------------------------------------
-- Briefings and situation reports (Projects 18, 19)
-- ---------------------------------------------------------------------------

-- Sample field updates from teams; reported figures may conflict with records.
CREATE TABLE field_updates (
    update_id     TEXT PRIMARY KEY,         -- e.g. FU-0001
    reported_at   TEXT NOT NULL,
    area_id       TEXT REFERENCES areas(area_id),
    site_id       TEXT REFERENCES sites(site_id),
    team          TEXT NOT NULL,
    text          TEXT NOT NULL,
    figure_key    TEXT,                     -- e.g. households_reached
    figure_value  REAL
);

CREATE TABLE briefings (
    briefing_id    TEXT PRIMARY KEY,        -- e.g. BRF-0001
    briefing_date  TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('draft','approved')),
    content_md     TEXT NOT NULL,
    conflicts_json TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL REFERENCES staff(staff_id),
    approved_by    TEXT REFERENCES staff(staff_id),
    approved_at    TEXT
);

-- Each figure in a briefing and the record(s) it came from.
CREATE TABLE briefing_sources (
    briefing_id       TEXT NOT NULL REFERENCES briefings(briefing_id),
    figure_key        TEXT NOT NULL,
    figure_value      REAL NOT NULL,
    unit              TEXT NOT NULL,
    source_table      TEXT NOT NULL,
    source_record_ids TEXT NOT NULL,        -- comma list of record IDs, or a query description
    PRIMARY KEY (briefing_id, figure_key, source_table)
);

-- Library of fictional past situation reports (19).
CREATE TABLE sitreps (
    sitrep_id    TEXT PRIMARY KEY,          -- e.g. SITREP-001
    title        TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    area_id      TEXT REFERENCES areas(area_id),
    body_md      TEXT NOT NULL
);

CREATE TABLE sitrep_passages (
    passage_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    sitrep_id   TEXT NOT NULL REFERENCES sitreps(sitrep_id),
    seq         INTEGER NOT NULL,
    heading     TEXT,
    text        TEXT NOT NULL,
    UNIQUE (sitrep_id, seq)
);

-- ---------------------------------------------------------------------------
-- Platform: audit, alerts, jobs, health (Project 20)
-- ---------------------------------------------------------------------------

-- Every change made through the apps or the API.
CREATE TABLE audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    actor_id     TEXT NOT NULL,             -- staff_id, or 'system' for scheduled jobs
    actor_role   TEXT NOT NULL,
    channel      TEXT NOT NULL,             -- e.g. app:02, api, scheduler, seed
    action       TEXT NOT NULL,             -- create, update, delete, approve, merge, resolve, ...
    entity_table TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    before_json  TEXT,
    after_json   TEXT,
    reason       TEXT
);

CREATE TABLE alert_rules (
    rule_id      TEXT PRIMARY KEY,          -- e.g. RULE-STOCK
    name         TEXT NOT NULL,
    rule_type    TEXT NOT NULL CHECK (rule_type IN ('stock_below','overdue_callbacks','stale_position','pipeline_failed')),
    threshold    REAL NOT NULL,             -- meaning depends on rule_type, see description
    params_json  TEXT NOT NULL DEFAULT '{}',
    description  TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    enabled      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE alerts (
    alert_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id          TEXT NOT NULL REFERENCES alert_rules(rule_id),
    fired_at         TEXT NOT NULL,
    entity_ref       TEXT NOT NULL,         -- e.g. WH-01/ITM-FP-STD or VEH-03
    observed_value   REAL,
    threshold        REAL NOT NULL,
    message          TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    acknowledged_by  TEXT REFERENCES staff(staff_id),
    acknowledged_at  TEXT,
    resolved_at      TEXT
);

-- Scheduled jobs the health page lists, and every run of each.
CREATE TABLE scheduled_jobs (
    job_name          TEXT PRIMARY KEY,     -- pipeline_ingest, alert_evaluation, position_simulator, db_backup
    description       TEXT NOT NULL,
    interval_seconds  INTEGER NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE job_runs (
    job_run_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name     TEXT NOT NULL REFERENCES scheduled_jobs(job_name),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running','success','failed')),
    trigger      TEXT NOT NULL CHECK (trigger IN ('scheduled','manual')),
    detail       TEXT
);

-- Services the health page lists (apps, API, scheduler, simulator).
CREATE TABLE service_heartbeats (
    service_name  TEXT PRIMARY KEY,
    last_seen_at  TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok','degraded','down')),
    detail        TEXT
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX ix_calls_received ON calls(received_at);
CREATE INDEX ix_calls_complaint ON calls(complaint_id);
CREATE INDEX ix_requests_status ON service_requests(status);
CREATE INDEX ix_movements_wh_item ON stock_movements(warehouse_id, item_id);
CREATE INDEX ix_distributions_site_date ON distributions(site_id, distribution_date);
CREATE INDEX ix_distributions_household ON distributions(household_id);
CREATE INDEX ix_positions_vehicle_time ON position_reports(vehicle_id, reported_at);
CREATE INDEX ix_incidents_area_time ON incidents(area_id, occurred_at);
CREATE INDEX ix_advisories_area ON advisories(area_id, decided_at);
CREATE INDEX ix_audit_entity ON audit_log(entity_table, entity_id);
CREATE INDEX ix_alerts_rule ON alerts(rule_id, status);
CREATE INDEX ix_job_runs_job ON job_runs(job_name, started_at);

-- ---------------------------------------------------------------------------
-- Views shared by several projects
-- ---------------------------------------------------------------------------

-- On-hand stock per warehouse and item: received minus dispatched (10, 16, 17, 20).
CREATE VIEW v_stock_on_hand AS
SELECT w.warehouse_id, i.item_id,
       COALESCE(SUM(CASE WHEN m.movement_type = 'received'   THEN m.quantity END), 0) AS received,
       COALESCE(SUM(CASE WHEN m.movement_type = 'dispatched' THEN m.quantity END), 0) AS dispatched,
       COALESCE(SUM(CASE WHEN m.movement_type = 'received'   THEN m.quantity END), 0)
     - COALESCE(SUM(CASE WHEN m.movement_type = 'dispatched' THEN m.quantity END), 0) AS on_hand
FROM warehouses w
CROSS JOIN items i
LEFT JOIN stock_movements m ON m.warehouse_id = w.warehouse_id AND m.item_id = i.item_id
GROUP BY w.warehouse_id, i.item_id;

-- Number of calls per complaint (02): the complaint count stays one per complaint.
CREATE VIEW v_complaint_call_counts AS
SELECT c.complaint_id, c.topic, c.status, COUNT(k.call_id) AS call_count
FROM complaints c
LEFT JOIN calls k ON k.complaint_id = c.complaint_id
GROUP BY c.complaint_id;

-- Latest position per vehicle (14, 17, 20).
CREATE VIEW v_last_position AS
SELECT p.vehicle_id, p.convoy_id, p.reported_at, p.x_km, p.y_km, p.speed_kmh, p.source
FROM position_reports p
JOIN (SELECT vehicle_id, MAX(reported_at) AS max_at FROM position_reports GROUP BY vehicle_id) last
  ON last.vehicle_id = p.vehicle_id AND last.max_at = p.reported_at;

-- Current advisory per area: newest active decision (15, 16, 17).
CREATE VIEW v_current_advisory AS
SELECT a.*
FROM advisories a
JOIN (SELECT area_id, MAX(decided_at) AS max_at FROM advisories WHERE active = 1 GROUP BY area_id) cur
  ON cur.area_id = a.area_id AND cur.max_at = a.decided_at
WHERE a.active = 1;
