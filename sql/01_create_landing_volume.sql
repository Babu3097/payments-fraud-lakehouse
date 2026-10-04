-- Landing zone: raw source files exactly as received, before Auto Loader ingests them.
-- A managed volume: Unity Catalog owns the storage (Free Edition has no custom storage locations).
CREATE VOLUME IF NOT EXISTS workspace.bronze.landing
COMMENT 'Raw source files before ingestion: PaySim, daily feeds, bank holidays';
