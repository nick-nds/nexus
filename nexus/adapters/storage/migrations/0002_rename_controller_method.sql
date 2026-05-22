-- 0002 — rename node kind "controller_method" → "method".
--
-- Audit P0-3: ``controller_method`` was hardcoded on every method node
-- regardless of container class. For packages with zero controllers
-- (BFF libraries, domain packages) the label was misleading. The
-- enum has been renamed to ``NodeKind.METHOD = "method"`` so the
-- ``node_kind`` field in tool responses reflects reality.
--
-- This migration updates any pre-existing rows in-place so old indexes
-- still load after upgrade. It is idempotent: running it twice is a
-- no-op since no rows match after the first run.

UPDATE nodes SET kind = 'method' WHERE kind = 'controller_method';
