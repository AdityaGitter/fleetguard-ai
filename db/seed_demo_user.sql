-- Run after postgres_schema.sql. Creates one fleet + one fleet_manager user
-- so the UI's default demo credentials (manager@fleet.demo / demo1234) work
-- out of the box. Password hash below is bcrypt("demo1234").
INSERT INTO fleet (fleet_id, name, segment)
VALUES ('11111111-1111-1111-1111-111111111111', 'Demo Fleet Co', 'commercial')
ON CONFLICT DO NOTHING;

INSERT INTO app_user (fleet_id, email, password_hash, role)
VALUES (
    '11111111-1111-1111-1111-111111111111',
    'manager@fleet.demo',
    '$2b$12$KIXQ2n0lWZ2eJv3H9Y0Y4uQvY0m1E4v0wq8t6z0m9E4v0wq8t6z0m',  -- placeholder; regenerate with hash_password()
    'fleet_manager'
)
ON CONFLICT DO NOTHING;

-- NOTE: the placeholder hash above is illustrative only. Before first run,
-- generate a real one with:
--   python -c "from api.auth import hash_password; print(hash_password('demo1234'))"
-- and paste it in here, or call the seed loader (scripts/seed_all.py) which
-- does this automatically.
