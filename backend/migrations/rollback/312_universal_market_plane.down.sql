DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM market_plane_events LIMIT 1) OR
    EXISTS(SELECT 1 FROM market_plane_registry LIMIT 1) OR
    EXISTS(SELECT 1 FROM market_plane_certification LIMIT 1) THEN
   RAISE EXCEPTION 'Refusing rollback 312: Universal Market Plane contains evidence/state';
 END IF;
END $$;
DROP TABLE IF EXISTS market_plane_events;
DROP TABLE IF EXISTS market_plane_certification;
DROP TABLE IF EXISTS market_plane_registry;
