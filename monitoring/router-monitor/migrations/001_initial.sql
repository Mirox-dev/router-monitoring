CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS routers (
  id varchar(64) PRIMARY KEY,
  display_name varchar(128) NOT NULL,
  model varchar(128) NOT NULL,
  gateway varchar(128) NOT NULL,
  reverse_port integer NOT NULL,
  egress_policy varchar(32) NOT NULL DEFAULT 'foreign',
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS health_samples (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id varchar(64) NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
  collected_at timestamptz NOT NULL,
  window_started_at timestamptz NOT NULL,
  window_finished_at timestamptz NOT NULL,
  transport_ok boolean NOT NULL DEFAULT false,
  process_ok boolean NOT NULL DEFAULT false,
  service_ok boolean NOT NULL DEFAULT false,
  tun_ok boolean NOT NULL DEFAULT false,
  foreign_ip_ok boolean NOT NULL DEFAULT false,
  load1 double precision,
  cpu_percent double precision,
  vsz_kb integer,
  rss_kb integer,
  mem_available_kb integer,
  disk_used_percent double precision,
  restart_count integer,
  checks jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text
);
CREATE INDEX IF NOT EXISTS health_samples_router_time_idx ON health_samples(router_id, collected_at DESC);

CREATE TABLE IF NOT EXISTS monitor_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id varchar(64) NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
  kind varchar(64) NOT NULL,
  severity varchar(16) NOT NULL DEFAULT 'info',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS baseline_profiles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id varchar(64) NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
  metric varchar(64) NOT NULL,
  window varchar(32) NOT NULL,
  median_value double precision NOT NULL,
  p95_value double precision NOT NULL,
  mad_value double precision NOT NULL,
  sample_count integer NOT NULL DEFAULT 0,
  learning boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(router_id, metric, window)
);

CREATE TABLE IF NOT EXISTS monitor_commands (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id varchar(64) NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
  action varchar(32) NOT NULL CHECK (action IN ('health_check', 'restart_sing_box')),
  requested_by varchar(128) NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'pending',
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz
);
