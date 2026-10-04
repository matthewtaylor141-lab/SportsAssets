-- LAB-B CITATION INTEGRITY: RETROSPECTIVE OVER THE STORED AGENT ANSWERS
-- (SELECT only; PM question E: how often do agents cite the wrong
-- supporting fact?).
--
-- THE SAME RULES AS THE PYTHON VERIFIER, profile RETRO_PORT
-- (sportsassets/lab/citation_integrity.py verify(profile="RETRO_PORT"));
-- tests/test_lab_citation_integrity_retro_parity.py runs THIS file against a
-- seeded database and requires the tallies to equal the Python verifier's,
-- row for row. The profile checks the figures (measure-blind) and the
-- CURRENT / SUPERSEDED status of every cited sentence; ids, timestamps,
-- codes, measure words and agent attributions are checked live by the full
-- profile only.
--
--   1. every COMPLETE assistant answer with an [F#] citation, its provider
--      mode, the question it answered and the facts it CITED (migration 180
--      persists only the cited facts, never the full list it was composed
--      from);
--   2. each cited fact's verbatim quotation in the answer is kept as one
--      unit (the whitespace after . ! ? and every line break inside it is
--      neutralised), then the answer is split into sentences;
--   3. each sentence's citation groups; the clause before a group is bound
--      to it, the tail after the last group to the last group, and a figure
--      may also be supported by the group just before its own;
--   4. figures: a bare 0 / 1 / 2 is a counting word and a figure the
--      question states is an echo -- neither is material; a figure is
--      supported when a bound fact holds it exactly or rounded to the
--      precision written (half-up or half-even, admitted only with two or
--      more significant digits or within 1%), x100 / /100 with a percent
--      unit;
--   5. verdict per material sentence: NO_CITATION, INSUFFICIENT_SUPPORT (no
--      stored fact holds it), WRONG_FACT (another stored fact holds it, or a
--      CURRENT-marked record is called superseded), STALE_STATE_CITATION (a
--      SUPERSEDED / HISTORICAL record cited for the current state, or the
--      figure sits in a current record while a superseded one is cited);
--   6. UNVERIFIABLE_CITED_FACT_NOT_STORED: a failing sentence citing a fact
--      id the stored record does not hold (before the comma-list fix,
--      "[F212, F213]" citations were never stored) cannot be judged from
--      the record -- counted apart, out of every rate;
--   7. INFERRED_UNCITED_SOURCE: an LLM-mode answer passed the full-list
--      figure check when it was published, so a figure no CITED fact holds
--      was held by an UNCITED fact -- the sentence cited the wrong or too
--      few facts (the upper bound of the wrong-fact rate).
-- Bounded: answers at or before now(); the samples are LIMITed.

\echo === read time and verifier
SELECT now() AS read_at, 'CITATION_INTEGRITY_V1' AS verifier,
       'RETRO_PORT' AS profile;

\echo === tally by agent and provider mode (ALL rows are the totals)
WITH RECURSIVE ans AS (
    SELECT m.message_id, m.agent_id,
           coalesce(m.provider->>'mode', m.outcome, 'UNKNOWN') AS mode,
           m.body,
           CASE WHEN jsonb_typeof(m.facts) = 'array' THEN m.facts
                ELSE '[]'::jsonb END AS facts,
           coalesce(u.body, '') AS question
      FROM agent_chat_messages m
      LEFT JOIN agent_chat_messages u ON u.message_id = m.in_reply_to
     WHERE m.role = 'ASSISTANT' AND m.status = 'COMPLETE'
       AND m.body ~ '\[F[0-9]+\]' AND m.at <= now()
), fct AS (
    SELECT a.message_id,
           row_number() OVER (PARTITION BY a.message_id ORDER BY e.ord) AS k,
           e.f->>'fact_id' AS fid, coalesce(e.f->>'text', '') AS ftext,
           coalesce(e.f->>'record_id', '') AS rid,
           coalesce(e.f->>'field', '') AS field, e.f->'value' AS fvalue
      FROM ans a, jsonb_array_elements(a.facts) WITH ORDINALITY AS e(f, ord)
     WHERE jsonb_typeof(e.f) = 'object' AND coalesce(e.f->>'fact_id', '') <> ''
), kfact AS (
    SELECT DISTINCT ON (message_id, fid) message_id, fid, ftext, rid, field,
           fvalue,
           (ftext ~ '\ySUPERSEDED\y' OR ftext ~ '\yHISTORICAL\y'
            OR field = 'superseded_review') AS stale,
           (ftext ~ '\yCURRENT\y' AND NOT (ftext ~ '\ySUPERSEDED\y'
            OR ftext ~ '\yHISTORICAL\y' OR field = 'superseded_review'))
             AS curr
      FROM fct ORDER BY message_id, fid, k
), fnum AS (
    SELECT kf.message_id, kf.fid, n.num
      FROM kfact kf, LATERAL (
          SELECT replace(mm[2], ',', '')::numeric AS num
            FROM regexp_matches(kf.ftext, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
          UNION ALL
          SELECT replace(mm[2], ',', '')::numeric
            FROM regexp_matches(kf.rid, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
          UNION ALL
          SELECT (kf.fvalue #>> '{}')::numeric
           WHERE jsonb_typeof(kf.fvalue) = 'number') n
), masked AS (
    SELECT a.message_id, 0::bigint AS k, a.body FROM ans a
    UNION ALL
    SELECT m.message_id, m.k + 1,
           CASE WHEN length(f.ftext) >= 12 AND position(f.ftext IN m.body) > 0
                THEN replace(m.body, f.ftext,
                             replace(regexp_replace(f.ftext,
                                 '([.!?])[ \t\r\n\f\v]', '\1' || chr(3), 'g'),
                                 chr(10), chr(3)))
                ELSE m.body END
      FROM masked m JOIN fct f ON f.message_id = m.message_id AND f.k = m.k + 1
), mbody AS (
    SELECT DISTINCT ON (message_id) message_id, body
      FROM masked ORDER BY message_id, k DESC
), sent0 AS (
    SELECT mb.message_id, s.ord AS sidx,
           replace(btrim(s.piece, E' \t\r\n\f\v'), chr(3), ' ') AS stext
      FROM mbody mb, regexp_split_to_table(mb.body,
           '(?<=[.!?])[ \t\r\n\f\v]+(?=[A-Z0-9"“$(])|\n+')
           WITH ORDINALITY AS s(piece, ord)
     WHERE btrim(s.piece, E' \t\r\n\f\v') <> ''
), sent AS (
    SELECT * FROM sent0
     WHERE stext NOT LIKE 'Records-only answer — the AI answer was not used%'
       AND stext NOT LIKE '(Integrity check:%'
), piece AS (
    SELECT st.message_id, st.sidx, p.ord AS pidx,
           CASE WHEN position(chr(1) IN p.piece) > 0
                THEN split_part(p.piece, chr(1), 1) ELSE p.piece END AS stxt,
           CASE WHEN position(chr(1) IN p.piece) > 0
                THEN split_part(p.piece, chr(1), 2) END AS gtxt
      FROM sent st, regexp_split_to_table(regexp_replace(st.stext,
           '(?:\[F[0-9]+(?:[ ]*[,;][ ]*F[0-9]+)*\][ \t,]*)+',
           chr(1) || '\&' || chr(2), 'g'), chr(2)) WITH ORDINALITY AS p(piece, ord)
), grp AS (
    SELECT message_id, sidx, pidx, gtxt,
           row_number() OVER (PARTITION BY message_id, sidx ORDER BY pidx) AS gi
      FROM piece WHERE gtxt IS NOT NULL
), ngroups AS (
    SELECT st.message_id, st.sidx, count(g.gi) AS n
      FROM sent st LEFT JOIN grp g USING (message_id, sidx)
     GROUP BY st.message_id, st.sidx
), gid AS (
    SELECT DISTINCT g.message_id, g.sidx, g.gi,
           'F' || (mm[1]::numeric)::text AS fid
      FROM grp g, regexp_matches(g.gtxt, 'F([0-9]+)', 'g') AS mm
), seg AS (
    -- a clause before a group: bound to that group (gi), the group before
    -- it (gi - 1) also admitted for figures; the tail: the last group only
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, g.gi AS gi,
           CASE WHEN g.gi >= 2 THEN g.gi - 1 END AS gprev
      FROM piece p JOIN grp g USING (message_id, sidx, pidx)
    UNION ALL
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, ng.n, NULL
      FROM piece p JOIN ngroups ng USING (message_id, sidx)
     WHERE p.gtxt IS NULL AND ng.n > 0
       AND btrim(p.stxt, E' \t\r\n\f\v') <> ''
    UNION ALL
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, NULL, NULL
      FROM piece p JOIN ngroups ng USING (message_id, sidx)
     WHERE ng.n = 0
), qnum AS (
    SELECT DISTINCT a.message_id, replace(mm[2], ',', '')::numeric AS num
      FROM ans a, regexp_matches(a.question, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
), item0 AS (
    SELECT s.message_id, s.sidx, s.pidx, s.gi, s.gprev, n.ord AS ino,
           replace(n.mm[2], ',', '') AS xtxt,
           replace(n.mm[2], ',', '')::numeric AS x,
           CASE WHEN position('.' IN n.mm[2]) > 0
                THEN length(split_part(n.mm[2], '.', 2)) ELSE 0 END AS d,
           (coalesce(n.mm[3], '') ~ '%|pp|percent|cent|¢') AS pct,
           (n.mm[1] = '' AND position('.' IN n.mm[2]) = 0
            AND btrim(coalesce(n.mm[3], ''), E' \t\r\n\f\v') = '') AS bare
      FROM seg s, regexp_matches(s.stxt, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') WITH ORDINALITY AS n(mm, ord)
), item AS (
    SELECT i.*,
           length(ltrim(replace(i.xtxt, '.', ''), '0')) AS sig
      FROM item0 i
     WHERE NOT (i.bare AND i.x IN (0, 1, 2))
       AND NOT EXISTS (SELECT 1 FROM qnum q
                        WHERE q.message_id = i.message_id AND q.num = i.x)
), cand AS (
    -- (item, fact) pairs where the fact holds the figure
    SELECT DISTINCT i.message_id, i.sidx, i.pidx, i.ino, f.fid
      FROM item i JOIN fnum f ON f.message_id = i.message_id,
           LATERAL (SELECT abs(f.num) AS c
                    UNION ALL SELECT abs(f.num) * 100 WHERE i.pct
                    UNION ALL SELECT abs(f.num) / 100 WHERE i.pct) cc,
           LATERAL (SELECT cc.c * power(10::numeric, i.d) AS s) sc,
           LATERAL (SELECT floor(sc.s) AS fl) fl
     WHERE cc.c = i.x
        OR ((round(cc.c, i.d) = i.x
             OR (CASE WHEN sc.s - fl.fl > 0.5 THEN fl.fl + 1
                      WHEN sc.s - fl.fl < 0.5 THEN fl.fl
                      WHEN mod(fl.fl, 2) = 0 THEN fl.fl
                      ELSE fl.fl + 1 END) / power(10::numeric, i.d) = i.x)
            AND (i.sig >= 2 OR abs(cc.c - i.x) <= abs(i.x) / 100))
), ipool AS (
    -- the known facts each figure is bound to
    SELECT DISTINCT i.message_id, i.sidx, i.pidx, i.ino, g.fid
      FROM item i JOIN gid g ON g.message_id = i.message_id
       AND g.sidx = i.sidx AND (g.gi = i.gi OR g.gi = i.gprev)
      JOIN kfact kf ON kf.message_id = g.message_id AND kf.fid = g.fid
), ires AS (
    SELECT i.message_id, i.sidx, i.pidx, i.ino, i.gi,
           EXISTS (SELECT 1 FROM ipool p JOIN cand c USING
                   (message_id, sidx, pidx, ino, fid)
                    WHERE p.message_id = i.message_id AND p.sidx = i.sidx
                      AND p.pidx = i.pidx AND p.ino = i.ino) AS ok,
           EXISTS (SELECT 1 FROM cand c
                    WHERE c.message_id = i.message_id AND c.sidx = i.sidx
                      AND c.pidx = i.pidx AND c.ino = i.ino
                      AND NOT EXISTS (SELECT 1 FROM ipool p
                                       WHERE p.message_id = c.message_id
                                         AND p.sidx = c.sidx
                                         AND p.pidx = c.pidx
                                         AND p.ino = c.ino
                                         AND p.fid = c.fid)) AS has_alt,
           EXISTS (SELECT 1 FROM ipool p JOIN kfact kf
                   ON kf.message_id = p.message_id AND kf.fid = p.fid
                    WHERE p.message_id = i.message_id AND p.sidx = i.sidx
                      AND p.pidx = i.pidx AND p.ino = i.ino
                      AND kf.stale AND NOT kf.curr) AS pool_stale,
           EXISTS (SELECT 1 FROM cand c JOIN kfact kf
                   ON kf.message_id = c.message_id AND kf.fid = c.fid
                    WHERE c.message_id = i.message_id AND c.sidx = i.sidx
                      AND c.pidx = i.pidx AND c.ino = i.ino AND NOT kf.stale
                      AND NOT EXISTS (SELECT 1 FROM ipool p
                                       WHERE p.message_id = c.message_id
                                         AND p.sidx = c.sidx
                                         AND p.pidx = c.pidx
                                         AND p.ino = c.ino
                                         AND p.fid = c.fid)) AS alt_fresh
      FROM item i
), ifail AS (
    SELECT message_id, sidx,
           CASE WHEN gi IS NULL THEN 1
                WHEN NOT has_alt THEN 2
                WHEN pool_stale AND alt_fresh THEN 4
                ELSE 3 END AS sev
      FROM ires WHERE NOT ok OR gi IS NULL
), stat AS (
    SELECT s.message_id, s.sidx, s.pidx, s.gi,
           (s.stxt ~ '\yCURRENT\y' OR (s.stxt ~* '\y(reviews?|decisions?|recommendations?|management|states?|status|calls?|positions?|orders?)\y'
             AND s.stxt ~* '\y(current|currently|latest|newest|now|live|still)\y')) AS cur,
           (s.stxt ~ '\ySUPERSEDED\y' OR (s.stxt ~* '\y(reviews?|decisions?|recommendations?|management|states?|status|calls?|positions?|orders?)\y'
             AND s.stxt ~* '\y(superseded|older|previous|earlier|prior|outdated|historical)\y')) AS sup
      FROM seg s
), sfail AS (
    -- status, checked against the clause's own group only
    SELECT st.message_id, st.sidx,
           CASE WHEN st.cur THEN 4 ELSE 3 END AS sev
      FROM stat st
     WHERE st.cur <> st.sup AND st.gi IS NOT NULL
       AND EXISTS (SELECT 1 FROM gid g JOIN kfact kf
                   ON kf.message_id = g.message_id AND kf.fid = g.fid
                    WHERE g.message_id = st.message_id AND g.sidx = st.sidx
                      AND g.gi = st.gi)
       AND NOT EXISTS (SELECT 1 FROM gid g JOIN kfact kf
                       ON kf.message_id = g.message_id AND kf.fid = g.fid
                        WHERE g.message_id = st.message_id
                          AND g.sidx = st.sidx AND g.gi = st.gi
                          AND CASE WHEN st.cur THEN NOT kf.stale
                                   ELSE NOT kf.curr END)
), smat AS (
    SELECT st.message_id, st.sidx, ng.n AS ngroups,
           EXISTS (SELECT 1 FROM item i WHERE i.message_id = st.message_id
                     AND i.sidx = st.sidx) AS has_num,
           EXISTS (SELECT 1 FROM stat x WHERE x.message_id = st.message_id
                     AND x.sidx = st.sidx AND x.cur <> x.sup) AS has_status
      FROM sent st JOIN ngroups ng USING (message_id, sidx)
), sverdict AS (
    SELECT sm.message_id, sm.sidx, sm.ngroups,
           CASE WHEN NOT (sm.has_num OR (sm.ngroups > 0 AND sm.has_status))
                THEN NULL
                ELSE coalesce((SELECT max(sev) FROM (
                        SELECT sev FROM ifail f WHERE f.message_id =
                          sm.message_id AND f.sidx = sm.sidx
                        UNION ALL
                        SELECT sev FROM sfail f WHERE f.message_id =
                          sm.message_id AND f.sidx = sm.sidx) z), 0) END AS sev,
           (SELECT count(*) FROM ifail f WHERE f.message_id = sm.message_id
              AND f.sidx = sm.sidx AND f.sev = 2) AS n_insufficient,
           EXISTS (SELECT 1 FROM gid g WHERE g.message_id = sm.message_id
                     AND g.sidx = sm.sidx
                     AND NOT EXISTS (SELECT 1 FROM kfact kf
                                      WHERE kf.message_id = g.message_id
                                        AND kf.fid = g.fid)) AS unk
      FROM smat sm
), per_answer AS (
    SELECT a.message_id, a.agent_id, a.mode,
           (SELECT count(*) FROM sent s WHERE s.message_id = a.message_id)
             AS sentences,
           count(v.sev) AS material,
           count(v.sev) FILTER (WHERE v.sev <> 1) AS cited_material,
           count(*) FILTER (WHERE v.sev = 0) AS pass,
           count(*) FILTER (WHERE v.sev = 3 AND NOT v.unk) AS wrong_fact,
           count(*) FILTER (WHERE v.sev = 2 AND NOT v.unk) AS insufficient,
           count(*) FILTER (WHERE v.sev = 4 AND NOT v.unk) AS stale,
           count(*) FILTER (WHERE v.sev = 5 AND NOT v.unk) AS entity,
           count(*) FILTER (WHERE v.sev = 1) AS no_citation,
           count(*) FILTER (WHERE v.sev > 0 AND v.unk) AS unverifiable,
           count(*) FILTER (WHERE v.sev = 2 AND a.mode = 'LLM'
                              AND NOT v.unk) AS inferred
      FROM ans a LEFT JOIN sverdict v ON v.message_id = a.message_id
     GROUP BY a.message_id, a.agent_id, a.mode
)
SELECT coalesce(agent_id, 'ALL') AS agent, coalesce(mode, 'ALL') AS mode,
       count(*) AS answers,
       count(*) FILTER (WHERE material > 0) AS answers_with_material,
       count(*) FILTER (WHERE cited_material > 0)
         AS answers_with_cited_material,
       sum(sentences) AS sentences, sum(material) AS material,
       sum(cited_material) AS cited_material, sum(pass) AS pass,
       sum(wrong_fact) AS wrong_fact, sum(insufficient) AS insufficient,
       sum(stale) AS stale, sum(entity) AS entity,
       sum(no_citation) AS no_citation,
       sum(unverifiable) AS unverifiable_not_stored,
       sum(inferred) AS inferred_uncited_source,
       count(*) FILTER (WHERE wrong_fact + stale + entity > 0)
         AS answers_with_wrong_support,
       count(*) FILTER (WHERE wrong_fact + stale + entity + inferred > 0)
         AS answers_with_wrong_or_inferred
  FROM per_answer
 GROUP BY GROUPING SETS ((agent_id, mode), (agent_id), (mode), ())
 ORDER BY agent_id NULLS LAST, mode NULLS LAST;


\echo === samples: sentences whose citation failed (the cited facts beside them)
WITH RECURSIVE ans AS (
    SELECT m.message_id, m.agent_id,
           coalesce(m.provider->>'mode', m.outcome, 'UNKNOWN') AS mode,
           m.body,
           CASE WHEN jsonb_typeof(m.facts) = 'array' THEN m.facts
                ELSE '[]'::jsonb END AS facts,
           coalesce(u.body, '') AS question
      FROM agent_chat_messages m
      LEFT JOIN agent_chat_messages u ON u.message_id = m.in_reply_to
     WHERE m.role = 'ASSISTANT' AND m.status = 'COMPLETE'
       AND m.body ~ '\[F[0-9]+\]' AND m.at <= now()
), fct AS (
    SELECT a.message_id,
           row_number() OVER (PARTITION BY a.message_id ORDER BY e.ord) AS k,
           e.f->>'fact_id' AS fid, coalesce(e.f->>'text', '') AS ftext,
           coalesce(e.f->>'record_id', '') AS rid,
           coalesce(e.f->>'field', '') AS field, e.f->'value' AS fvalue
      FROM ans a, jsonb_array_elements(a.facts) WITH ORDINALITY AS e(f, ord)
     WHERE jsonb_typeof(e.f) = 'object' AND coalesce(e.f->>'fact_id', '') <> ''
), kfact AS (
    SELECT DISTINCT ON (message_id, fid) message_id, fid, ftext, rid, field,
           fvalue,
           (ftext ~ '\ySUPERSEDED\y' OR ftext ~ '\yHISTORICAL\y'
            OR field = 'superseded_review') AS stale,
           (ftext ~ '\yCURRENT\y' AND NOT (ftext ~ '\ySUPERSEDED\y'
            OR ftext ~ '\yHISTORICAL\y' OR field = 'superseded_review'))
             AS curr
      FROM fct ORDER BY message_id, fid, k
), fnum AS (
    SELECT kf.message_id, kf.fid, n.num
      FROM kfact kf, LATERAL (
          SELECT replace(mm[2], ',', '')::numeric AS num
            FROM regexp_matches(kf.ftext, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
          UNION ALL
          SELECT replace(mm[2], ',', '')::numeric
            FROM regexp_matches(kf.rid, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
          UNION ALL
          SELECT (kf.fvalue #>> '{}')::numeric
           WHERE jsonb_typeof(kf.fvalue) = 'number') n
), masked AS (
    SELECT a.message_id, 0::bigint AS k, a.body FROM ans a
    UNION ALL
    SELECT m.message_id, m.k + 1,
           CASE WHEN length(f.ftext) >= 12 AND position(f.ftext IN m.body) > 0
                THEN replace(m.body, f.ftext,
                             replace(regexp_replace(f.ftext,
                                 '([.!?])[ \t\r\n\f\v]', '\1' || chr(3), 'g'),
                                 chr(10), chr(3)))
                ELSE m.body END
      FROM masked m JOIN fct f ON f.message_id = m.message_id AND f.k = m.k + 1
), mbody AS (
    SELECT DISTINCT ON (message_id) message_id, body
      FROM masked ORDER BY message_id, k DESC
), sent0 AS (
    SELECT mb.message_id, s.ord AS sidx,
           replace(btrim(s.piece, E' \t\r\n\f\v'), chr(3), ' ') AS stext
      FROM mbody mb, regexp_split_to_table(mb.body,
           '(?<=[.!?])[ \t\r\n\f\v]+(?=[A-Z0-9"“$(])|\n+')
           WITH ORDINALITY AS s(piece, ord)
     WHERE btrim(s.piece, E' \t\r\n\f\v') <> ''
), sent AS (
    SELECT * FROM sent0
     WHERE stext NOT LIKE 'Records-only answer — the AI answer was not used%'
       AND stext NOT LIKE '(Integrity check:%'
), piece AS (
    SELECT st.message_id, st.sidx, p.ord AS pidx,
           CASE WHEN position(chr(1) IN p.piece) > 0
                THEN split_part(p.piece, chr(1), 1) ELSE p.piece END AS stxt,
           CASE WHEN position(chr(1) IN p.piece) > 0
                THEN split_part(p.piece, chr(1), 2) END AS gtxt
      FROM sent st, regexp_split_to_table(regexp_replace(st.stext,
           '(?:\[F[0-9]+(?:[ ]*[,;][ ]*F[0-9]+)*\][ \t,]*)+',
           chr(1) || '\&' || chr(2), 'g'), chr(2)) WITH ORDINALITY AS p(piece, ord)
), grp AS (
    SELECT message_id, sidx, pidx, gtxt,
           row_number() OVER (PARTITION BY message_id, sidx ORDER BY pidx) AS gi
      FROM piece WHERE gtxt IS NOT NULL
), ngroups AS (
    SELECT st.message_id, st.sidx, count(g.gi) AS n
      FROM sent st LEFT JOIN grp g USING (message_id, sidx)
     GROUP BY st.message_id, st.sidx
), gid AS (
    SELECT DISTINCT g.message_id, g.sidx, g.gi,
           'F' || (mm[1]::numeric)::text AS fid
      FROM grp g, regexp_matches(g.gtxt, 'F([0-9]+)', 'g') AS mm
), seg AS (
    -- a clause before a group: bound to that group (gi), the group before
    -- it (gi - 1) also admitted for figures; the tail: the last group only
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, g.gi AS gi,
           CASE WHEN g.gi >= 2 THEN g.gi - 1 END AS gprev
      FROM piece p JOIN grp g USING (message_id, sidx, pidx)
    UNION ALL
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, ng.n, NULL
      FROM piece p JOIN ngroups ng USING (message_id, sidx)
     WHERE p.gtxt IS NULL AND ng.n > 0
       AND btrim(p.stxt, E' \t\r\n\f\v') <> ''
    UNION ALL
    SELECT p.message_id, p.sidx, p.pidx, p.stxt, NULL, NULL
      FROM piece p JOIN ngroups ng USING (message_id, sidx)
     WHERE ng.n = 0
), qnum AS (
    SELECT DISTINCT a.message_id, replace(mm[2], ',', '')::numeric AS num
      FROM ans a, regexp_matches(a.question, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') AS mm
), item0 AS (
    SELECT s.message_id, s.sidx, s.pidx, s.gi, s.gprev, n.ord AS ino,
           replace(n.mm[2], ',', '') AS xtxt,
           replace(n.mm[2], ',', '')::numeric AS x,
           CASE WHEN position('.' IN n.mm[2]) > 0
                THEN length(split_part(n.mm[2], '.', 2)) ELSE 0 END AS d,
           (coalesce(n.mm[3], '') ~ '%|pp|percent|cent|¢') AS pct,
           (n.mm[1] = '' AND position('.' IN n.mm[2]) = 0
            AND btrim(coalesce(n.mm[3], ''), E' \t\r\n\f\v') = '') AS bare
      FROM seg s, regexp_matches(s.stxt, '(?<![[:alnum:]_.:])[-−]?(\$?)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!:[0-9])([ \t\r\n\f\v]?(?:%|pp\y|percent(?:age)?|cents?\y|¢|seconds?\y|secs?\y|minutes?\y|mins?\y|contracts?\y|s\y|x\y))?', 'g') WITH ORDINALITY AS n(mm, ord)
), item AS (
    SELECT i.*,
           length(ltrim(replace(i.xtxt, '.', ''), '0')) AS sig
      FROM item0 i
     WHERE NOT (i.bare AND i.x IN (0, 1, 2))
       AND NOT EXISTS (SELECT 1 FROM qnum q
                        WHERE q.message_id = i.message_id AND q.num = i.x)
), cand AS (
    -- (item, fact) pairs where the fact holds the figure
    SELECT DISTINCT i.message_id, i.sidx, i.pidx, i.ino, f.fid
      FROM item i JOIN fnum f ON f.message_id = i.message_id,
           LATERAL (SELECT abs(f.num) AS c
                    UNION ALL SELECT abs(f.num) * 100 WHERE i.pct
                    UNION ALL SELECT abs(f.num) / 100 WHERE i.pct) cc,
           LATERAL (SELECT cc.c * power(10::numeric, i.d) AS s) sc,
           LATERAL (SELECT floor(sc.s) AS fl) fl
     WHERE cc.c = i.x
        OR ((round(cc.c, i.d) = i.x
             OR (CASE WHEN sc.s - fl.fl > 0.5 THEN fl.fl + 1
                      WHEN sc.s - fl.fl < 0.5 THEN fl.fl
                      WHEN mod(fl.fl, 2) = 0 THEN fl.fl
                      ELSE fl.fl + 1 END) / power(10::numeric, i.d) = i.x)
            AND (i.sig >= 2 OR abs(cc.c - i.x) <= abs(i.x) / 100))
), ipool AS (
    -- the known facts each figure is bound to
    SELECT DISTINCT i.message_id, i.sidx, i.pidx, i.ino, g.fid
      FROM item i JOIN gid g ON g.message_id = i.message_id
       AND g.sidx = i.sidx AND (g.gi = i.gi OR g.gi = i.gprev)
      JOIN kfact kf ON kf.message_id = g.message_id AND kf.fid = g.fid
), ires AS (
    SELECT i.message_id, i.sidx, i.pidx, i.ino, i.gi,
           EXISTS (SELECT 1 FROM ipool p JOIN cand c USING
                   (message_id, sidx, pidx, ino, fid)
                    WHERE p.message_id = i.message_id AND p.sidx = i.sidx
                      AND p.pidx = i.pidx AND p.ino = i.ino) AS ok,
           EXISTS (SELECT 1 FROM cand c
                    WHERE c.message_id = i.message_id AND c.sidx = i.sidx
                      AND c.pidx = i.pidx AND c.ino = i.ino
                      AND NOT EXISTS (SELECT 1 FROM ipool p
                                       WHERE p.message_id = c.message_id
                                         AND p.sidx = c.sidx
                                         AND p.pidx = c.pidx
                                         AND p.ino = c.ino
                                         AND p.fid = c.fid)) AS has_alt,
           EXISTS (SELECT 1 FROM ipool p JOIN kfact kf
                   ON kf.message_id = p.message_id AND kf.fid = p.fid
                    WHERE p.message_id = i.message_id AND p.sidx = i.sidx
                      AND p.pidx = i.pidx AND p.ino = i.ino
                      AND kf.stale AND NOT kf.curr) AS pool_stale,
           EXISTS (SELECT 1 FROM cand c JOIN kfact kf
                   ON kf.message_id = c.message_id AND kf.fid = c.fid
                    WHERE c.message_id = i.message_id AND c.sidx = i.sidx
                      AND c.pidx = i.pidx AND c.ino = i.ino AND NOT kf.stale
                      AND NOT EXISTS (SELECT 1 FROM ipool p
                                       WHERE p.message_id = c.message_id
                                         AND p.sidx = c.sidx
                                         AND p.pidx = c.pidx
                                         AND p.ino = c.ino
                                         AND p.fid = c.fid)) AS alt_fresh
      FROM item i
), ifail AS (
    SELECT message_id, sidx,
           CASE WHEN gi IS NULL THEN 1
                WHEN NOT has_alt THEN 2
                WHEN pool_stale AND alt_fresh THEN 4
                ELSE 3 END AS sev
      FROM ires WHERE NOT ok OR gi IS NULL
), stat AS (
    SELECT s.message_id, s.sidx, s.pidx, s.gi,
           (s.stxt ~ '\yCURRENT\y' OR (s.stxt ~* '\y(reviews?|decisions?|recommendations?|management|states?|status|calls?|positions?|orders?)\y'
             AND s.stxt ~* '\y(current|currently|latest|newest|now|live|still)\y')) AS cur,
           (s.stxt ~ '\ySUPERSEDED\y' OR (s.stxt ~* '\y(reviews?|decisions?|recommendations?|management|states?|status|calls?|positions?|orders?)\y'
             AND s.stxt ~* '\y(superseded|older|previous|earlier|prior|outdated|historical)\y')) AS sup
      FROM seg s
), sfail AS (
    -- status, checked against the clause's own group only
    SELECT st.message_id, st.sidx,
           CASE WHEN st.cur THEN 4 ELSE 3 END AS sev
      FROM stat st
     WHERE st.cur <> st.sup AND st.gi IS NOT NULL
       AND EXISTS (SELECT 1 FROM gid g JOIN kfact kf
                   ON kf.message_id = g.message_id AND kf.fid = g.fid
                    WHERE g.message_id = st.message_id AND g.sidx = st.sidx
                      AND g.gi = st.gi)
       AND NOT EXISTS (SELECT 1 FROM gid g JOIN kfact kf
                       ON kf.message_id = g.message_id AND kf.fid = g.fid
                        WHERE g.message_id = st.message_id
                          AND g.sidx = st.sidx AND g.gi = st.gi
                          AND CASE WHEN st.cur THEN NOT kf.stale
                                   ELSE NOT kf.curr END)
), smat AS (
    SELECT st.message_id, st.sidx, ng.n AS ngroups,
           EXISTS (SELECT 1 FROM item i WHERE i.message_id = st.message_id
                     AND i.sidx = st.sidx) AS has_num,
           EXISTS (SELECT 1 FROM stat x WHERE x.message_id = st.message_id
                     AND x.sidx = st.sidx AND x.cur <> x.sup) AS has_status
      FROM sent st JOIN ngroups ng USING (message_id, sidx)
), sverdict AS (
    SELECT sm.message_id, sm.sidx, sm.ngroups,
           CASE WHEN NOT (sm.has_num OR (sm.ngroups > 0 AND sm.has_status))
                THEN NULL
                ELSE coalesce((SELECT max(sev) FROM (
                        SELECT sev FROM ifail f WHERE f.message_id =
                          sm.message_id AND f.sidx = sm.sidx
                        UNION ALL
                        SELECT sev FROM sfail f WHERE f.message_id =
                          sm.message_id AND f.sidx = sm.sidx) z), 0) END AS sev,
           (SELECT count(*) FROM ifail f WHERE f.message_id = sm.message_id
              AND f.sidx = sm.sidx AND f.sev = 2) AS n_insufficient,
           EXISTS (SELECT 1 FROM gid g WHERE g.message_id = sm.message_id
                     AND g.sidx = sm.sidx
                     AND NOT EXISTS (SELECT 1 FROM kfact kf
                                      WHERE kf.message_id = g.message_id
                                        AND kf.fid = g.fid)) AS unk
      FROM smat sm
), failing AS (
    SELECT v.message_id, v.sidx,
           CASE WHEN v.unk THEN 'UNVERIFIABLE_CITED_FACT_NOT_STORED'
                WHEN v.sev = 1 THEN 'NO_CITATION'
                WHEN v.sev = 2 THEN 'INSUFFICIENT_SUPPORT'
                WHEN v.sev = 3 THEN 'WRONG_FACT'
                WHEN v.sev = 4 THEN 'STALE_STATE_CITATION'
                ELSE 'ENTITY_MISMATCH' END AS verdict
      FROM sverdict v WHERE v.sev > 0
), ranked AS (
    SELECT f.*, a.agent_id, a.mode,
           row_number() OVER (PARTITION BY a.agent_id, f.verdict
                              ORDER BY f.message_id, f.sidx) AS rn
      FROM failing f JOIN ans a USING (message_id)
)
SELECT r.agent_id AS agent, r.mode, r.verdict, r.message_id, r.sidx,
       left(st.stext, 320) AS sentence,
       (SELECT string_agg(DISTINCT g.fid, ' ') FROM gid g
         WHERE g.message_id = r.message_id AND g.sidx = r.sidx) AS cited,
       (SELECT string_agg(i.xtxt || CASE WHEN NOT x.ok THEN '!' ELSE '' END,
                          ' ' ORDER BY i.pidx, i.ino)
          FROM item i JOIN ires x USING (message_id, sidx, pidx, ino)
         WHERE i.message_id = r.message_id AND i.sidx = r.sidx) AS figures,
       (SELECT string_agg(DISTINCT c.fid, ' ') FROM cand c
          JOIN ires x USING (message_id, sidx, pidx, ino)
         WHERE c.message_id = r.message_id AND c.sidx = r.sidx
           AND NOT x.ok) AS holders,
       (SELECT string_agg(g.fid || '=' || left(kf.ftext, 160), ' | ')
          FROM (SELECT DISTINCT fid FROM gid
                 WHERE message_id = r.message_id AND sidx = r.sidx) g
          JOIN kfact kf ON kf.message_id = r.message_id
           AND kf.fid = g.fid) AS cited_text
  FROM ranked r JOIN sent st USING (message_id, sidx)
 WHERE r.rn <= 8
 ORDER BY r.agent_id, r.verdict, r.message_id, r.sidx;
