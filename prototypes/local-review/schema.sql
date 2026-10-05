PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('individual', 'business')),
    purpose TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('under_review', 'needs_information', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('identity', 'address')),
    original_name TEXT NOT NULL,
    storage_name TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    is_sample INTEGER NOT NULL DEFAULT 0 CHECK (is_sample IN (0, 1)),
    uploaded_at TEXT NOT NULL,
    UNIQUE (case_id, kind)
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_case_id ON activity(case_id, id DESC);
