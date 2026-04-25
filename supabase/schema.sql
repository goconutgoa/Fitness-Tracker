-- =====================================================================
-- Fitness + Nutrition MCP — Supabase schema
-- Run once in the Supabase SQL editor after creating a fresh project.
-- =====================================================================

create extension if not exists "pgcrypto";
create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------------
-- Accounts (email/password managed by this server — NOT Supabase Auth,
-- so everything travels through the service role with explicit filtering)
-- ---------------------------------------------------------------------
create table if not exists app_users (
  id              uuid primary key default gen_random_uuid(),
  email           text unique not null,
  password_hash   text not null,
  timezone        text not null default 'UTC',
  created_at      timestamptz not null default now(),
  last_login_at   timestamptz
);

create index if not exists app_users_email_idx on app_users (lower(email));

-- ---------------------------------------------------------------------
-- OAuth 2.0 provider tables (for Claude.ai connector auth)
-- ---------------------------------------------------------------------
create table if not exists oauth_clients (
  client_id        text primary key,
  client_secret    text,                                -- null for public clients
  client_name      text,
  redirect_uris    text[] not null,
  grant_types      text[] not null default array['authorization_code','refresh_token'],
  token_endpoint_auth_method text not null default 'none',
  created_at       timestamptz not null default now()
);

create table if not exists oauth_auth_codes (
  code             text primary key,
  client_id        text not null references oauth_clients(client_id) on delete cascade,
  user_id          uuid not null references app_users(id) on delete cascade,
  redirect_uri     text not null,
  scope            text,
  code_challenge   text,
  code_challenge_method text,
  expires_at       timestamptz not null,
  consumed         boolean not null default false,
  created_at       timestamptz not null default now()
);

create table if not exists oauth_refresh_tokens (
  token            text primary key,
  client_id        text not null references oauth_clients(client_id) on delete cascade,
  user_id          uuid not null references app_users(id) on delete cascade,
  scope            text,
  expires_at       timestamptz not null,
  revoked          boolean not null default false,
  created_at       timestamptz not null default now()
);

create index if not exists oauth_refresh_user_idx on oauth_refresh_tokens (user_id);

-- ---------------------------------------------------------------------
-- NUTRITION
-- ---------------------------------------------------------------------
create table if not exists meals (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references app_users(id) on delete cascade,
  name            text not null,
  meal_type       text,                                  -- breakfast/lunch/dinner/snack
  calories        numeric,
  protein_g       numeric,
  carbs_g         numeric,
  fat_g           numeric,
  fiber_g         numeric,
  sugar_g         numeric,
  sodium_mg       numeric,
  notes           text,
  consumed_at     timestamptz not null default now(),
  created_at      timestamptz not null default now()
);
create index if not exists meals_user_date_idx on meals (user_id, consumed_at desc);

create table if not exists water_logs (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references app_users(id) on delete cascade,
  amount_ml       integer not null check (amount_ml > 0),
  consumed_at     timestamptz not null default now(),
  created_at      timestamptz not null default now()
);
create index if not exists water_logs_user_date_idx on water_logs (user_id, consumed_at desc);

create table if not exists nutrition_goals (
  user_id         uuid primary key references app_users(id) on delete cascade,
  calories        numeric,
  protein_g       numeric,
  carbs_g         numeric,
  fat_g           numeric,
  fiber_g         numeric,
  water_ml        integer,
  updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- FITNESS
-- ---------------------------------------------------------------------

-- Exercise catalog: seeded with common exercises, users can also add their own.
create table if not exists exercises (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid references app_users(id) on delete cascade,   -- null = global/seeded
  name            text not null,
  category        text,                                  -- strength/cardio/mobility
  primary_muscle  text,                                  -- chest/back/legs/shoulders/arms/core/glutes/full_body
  equipment       text,                                  -- barbell/dumbbell/cable/machine/bodyweight
  is_custom       boolean not null default false,
  created_at      timestamptz not null default now(),
  unique (user_id, name)
);
create index if not exists exercises_name_idx on exercises (lower(name));

-- A workout = a session. Each session contains many logged sets across exercises.
create table if not exists workouts (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references app_users(id) on delete cascade,
  name            text,                                  -- "Push Day", "Leg Day"
  notes           text,
  started_at      timestamptz not null default now(),
  ended_at        timestamptz,
  duration_min    integer,                               -- optional precomputed
  created_at      timestamptz not null default now()
);
create index if not exists workouts_user_date_idx on workouts (user_id, started_at desc);

create table if not exists workout_sets (
  id              uuid primary key default gen_random_uuid(),
  workout_id      uuid not null references workouts(id) on delete cascade,
  user_id         uuid not null references app_users(id) on delete cascade,
  exercise_name   text not null,                         -- denormalized for simpler queries
  exercise_id     uuid references exercises(id) on delete set null,
  set_number      integer not null,
  reps            integer,
  weight_kg       numeric,
  rpe             numeric,                               -- rate of perceived exertion 1-10
  is_warmup       boolean not null default false,
  notes           text,
  created_at      timestamptz not null default now()
);
create index if not exists workout_sets_user_idx on workout_sets (user_id, created_at desc);
create index if not exists workout_sets_exercise_idx on workout_sets (user_id, lower(exercise_name), created_at desc);
create index if not exists workout_sets_workout_idx on workout_sets (workout_id, set_number);

create table if not exists cardio_sessions (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references app_users(id) on delete cascade,
  activity        text not null,                         -- running/cycling/swimming/rowing/elliptical/walking/hiit
  duration_min    numeric not null check (duration_min > 0),
  distance_km     numeric,
  calories        numeric,
  avg_heart_rate  integer,
  max_heart_rate  integer,
  notes           text,
  performed_at    timestamptz not null default now(),
  created_at      timestamptz not null default now()
);
create index if not exists cardio_user_date_idx on cardio_sessions (user_id, performed_at desc);

-- One row per user per local date.
create table if not exists step_logs (
  user_id         uuid not null references app_users(id) on delete cascade,
  log_date        date not null,
  steps           integer not null check (steps >= 0),
  distance_km     numeric,
  calories        numeric,
  updated_at      timestamptz not null default now(),
  primary key (user_id, log_date)
);

create table if not exists body_metrics (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references app_users(id) on delete cascade,
  measured_at     timestamptz not null default now(),
  weight_kg       numeric,
  body_fat_pct    numeric,
  waist_cm        numeric,
  chest_cm        numeric,
  arm_cm          numeric,
  thigh_cm        numeric,
  resting_hr      integer,
  notes           text,
  created_at      timestamptz not null default now()
);
create index if not exists body_metrics_user_date_idx on body_metrics (user_id, measured_at desc);

create table if not exists fitness_goals (
  user_id             uuid primary key references app_users(id) on delete cascade,
  daily_steps         integer,
  weekly_workouts     integer,
  weekly_cardio_min   integer,
  weekly_volume_kg    numeric,                           -- total weight-lifted target
  target_weight_kg    numeric,
  target_body_fat_pct numeric,
  updated_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Row-level security — defense in depth. Service role bypasses RLS,
-- but we enable it so any future use of anon/authenticated keys is safe.
-- ---------------------------------------------------------------------
alter table meals             enable row level security;
alter table water_logs        enable row level security;
alter table nutrition_goals   enable row level security;
alter table exercises         enable row level security;
alter table workouts          enable row level security;
alter table workout_sets      enable row level security;
alter table cardio_sessions   enable row level security;
alter table step_logs         enable row level security;
alter table body_metrics      enable row level security;
alter table fitness_goals     enable row level security;

do $$ begin
  create policy "own rows"  on meals           for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on water_logs      for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on nutrition_goals for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on workouts        for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on workout_sets    for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on cardio_sessions for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on step_logs       for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on body_metrics    for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own rows"  on fitness_goals   for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  create policy "own or global" on exercises   for all using (user_id is null or user_id = auth.uid()) with check (user_id = auth.uid());
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------
-- Seed a small global exercise catalog.
-- ---------------------------------------------------------------------
insert into exercises (user_id, name, category, primary_muscle, equipment) values
  (null, 'Barbell Back Squat', 'strength', 'legs', 'barbell'),
  (null, 'Barbell Front Squat', 'strength', 'legs', 'barbell'),
  (null, 'Deadlift', 'strength', 'back', 'barbell'),
  (null, 'Romanian Deadlift', 'strength', 'legs', 'barbell'),
  (null, 'Bench Press', 'strength', 'chest', 'barbell'),
  (null, 'Incline Bench Press', 'strength', 'chest', 'barbell'),
  (null, 'Overhead Press', 'strength', 'shoulders', 'barbell'),
  (null, 'Barbell Row', 'strength', 'back', 'barbell'),
  (null, 'Pull-up', 'strength', 'back', 'bodyweight'),
  (null, 'Chin-up', 'strength', 'back', 'bodyweight'),
  (null, 'Dip', 'strength', 'chest', 'bodyweight'),
  (null, 'Push-up', 'strength', 'chest', 'bodyweight'),
  (null, 'Dumbbell Bench Press', 'strength', 'chest', 'dumbbell'),
  (null, 'Dumbbell Row', 'strength', 'back', 'dumbbell'),
  (null, 'Dumbbell Shoulder Press', 'strength', 'shoulders', 'dumbbell'),
  (null, 'Lateral Raise', 'strength', 'shoulders', 'dumbbell'),
  (null, 'Bicep Curl', 'strength', 'arms', 'dumbbell'),
  (null, 'Hammer Curl', 'strength', 'arms', 'dumbbell'),
  (null, 'Tricep Pushdown', 'strength', 'arms', 'cable'),
  (null, 'Leg Press', 'strength', 'legs', 'machine'),
  (null, 'Leg Curl', 'strength', 'legs', 'machine'),
  (null, 'Leg Extension', 'strength', 'legs', 'machine'),
  (null, 'Calf Raise', 'strength', 'legs', 'machine'),
  (null, 'Lat Pulldown', 'strength', 'back', 'cable'),
  (null, 'Seated Cable Row', 'strength', 'back', 'cable'),
  (null, 'Hip Thrust', 'strength', 'glutes', 'barbell'),
  (null, 'Plank', 'strength', 'core', 'bodyweight'),
  (null, 'Hanging Leg Raise', 'strength', 'core', 'bodyweight')
on conflict do nothing;
