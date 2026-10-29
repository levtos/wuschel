-- Persistent versioned context/profile mappings; no TC credentials or title contents.
CREATE TABLE title_sync_mapping (
    key text PRIMARY KEY,
    payload jsonb NOT NULL
);
