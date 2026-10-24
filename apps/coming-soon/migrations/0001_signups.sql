-- Launch-notification signups, one row per email per site.
-- confirmed_at stays NULL until the double opt-in email is sent and clicked.
CREATE TABLE IF NOT EXISTS signups (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  site TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  UNIQUE (email, site)
);

CREATE INDEX IF NOT EXISTS signups_site ON signups (site);
