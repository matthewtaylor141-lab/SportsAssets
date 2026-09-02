-- THE POST-FILL PRICE PATH, SAMPLED.
--
-- The whale's edge is real and our copies of him return ~0%, because
-- his buy moves the ask and we pay that impact to get in. Whether any
-- fill rule can carry his edge turns on ONE curve nobody has measured:
-- the mean change in the ask at t seconds after his fill.
--
--   drifts UP   -> his information is still propagating; buy the
--                  post-impact ask NOW and the edge survives the impact
--   drifts DOWN -> the impact reverts; a resting bid at his price
--                  captures it and the IOC does not
--   flat        -> the market absorbed him instantly; no limit rule has
--                  edge and the lever is whale/market selection
--
-- We see his fill at -0.65s; the venue publishes it at ~281s and the
-- crowd that follows him acts then. So the offsets bracket that window.
-- One row per (attempted copy, offset). The sampler keys off live_orders
-- because that row carries the US slug and the intent the ask must be
-- read on; raw trades carry neither.
CREATE TABLE IF NOT EXISTS price_path (
    row_id      bigint            NOT NULL,
    t_s         integer           NOT NULL,
    ask         double precision,
    sampled_at  timestamptz       NOT NULL DEFAULT now(),
    PRIMARY KEY (row_id, t_s)
);

CREATE INDEX IF NOT EXISTS price_path_sampled_idx ON price_path (sampled_at);
