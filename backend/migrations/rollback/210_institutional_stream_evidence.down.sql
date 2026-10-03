-- Drops the stream runtime evidence and the same-book probe tables. They are
-- evidence only: nothing decides from them, and the P5 evidence endpoint
-- reports the stream evidence as ABSENT without them (fail closed).
DROP TABLE IF EXISTS institutional_same_book_probe;
DROP TABLE IF EXISTS institutional_stream_evidence;
