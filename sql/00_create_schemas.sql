-- One schema per medallion layer inside the workspace catalog (docs/decisions.md, ADR-002).
-- Not executed yet: we review and run it in Phase 2.
CREATE SCHEMA IF NOT EXISTS workspace.bronze
COMMENT 'Raw, append-only copies of source data';

CREATE SCHEMA IF NOT EXISTS workspace.silver
COMMENT 'Cleaned, typed, deduplicated data with quality expectations';

CREATE SCHEMA IF NOT EXISTS workspace.gold
COMMENT 'Star schema and KPI tables for reporting';
