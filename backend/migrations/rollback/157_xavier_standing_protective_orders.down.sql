-- down for 157. Refuses while any standing order plan is recorded: a plan
-- names an order that may have reached the venue, and its lifecycle events
-- are the only record of what was requested, filled and confirmed terminal.
DO $$
BEGIN
    IF to_regclass('bettor_standing_order_plans') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_standing_order_plans) THEN
        RAISE EXCEPTION 'standing order plans are recorded; 157 is not '
                        'rolled back over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_standing_capacity_reservations;
DROP TABLE IF EXISTS bettor_standing_order_events;
DROP TABLE IF EXISTS bettor_hedge_group_selection;
DROP TABLE IF EXISTS bettor_standing_order_plans;
DROP FUNCTION IF EXISTS bettor_standing_reservation_releases_on_terminal();
DROP FUNCTION IF EXISTS bettor_standing_event_is_a_record();
DROP FUNCTION IF EXISTS bettor_hedge_selection_is_a_record();
DROP FUNCTION IF EXISTS bettor_standing_plan_is_a_record();
