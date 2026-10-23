-- 002: a manager leads at most one team (docs/design.md §2), enforced rather than assumed.
--
-- Code that resolves "the team I lead" relies on this; without it, a manager with two teams
-- would get an arbitrary one. DEFERRABLE lets two managers swap teams inside one
-- transaction (SET CONSTRAINTS ... DEFERRED); otherwise it is checked immediately.

ALTER TABLE team
    ADD CONSTRAINT team_one_per_manager UNIQUE (tenant_id, manager_id)
    DEFERRABLE INITIALLY IMMEDIATE;
