-- READ-ONLY. Why mirror_shadow abandoned its tick after 10af406. SELECT only.
SELECT service, status, beat_at,
       detail->>'abandoned' abandoned, detail->>'abandon_reason' reason,
       detail->>'quote_client' quote_client,
       left((detail->'quote_misses')::text, 600) misses,
       left((detail->'exit_leg')::text, 300) exit_leg,
       left(detail::text, 2500) detail
  FROM service_heartbeats WHERE service = 'mirror_shadow';
