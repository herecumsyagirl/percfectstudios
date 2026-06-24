-- Run this in your Supabase SQL editor

create table if not exists users (
  id               bigserial primary key,
  username         text unique not null,
  password         text not null,
  birthday         text,
  api_key1         text unique,
  api_key2         text unique,
  images_today     int default 0,
  videos_today     int default 0,
  last_reset       timestamptz default now(),
  picture_credits  int default 0,
  video_credits    int default 0,
  created_at       timestamptz default now()
);

create table if not exists generations (
  id          bigserial primary key,
  user_id     bigint references users(id) on delete cascade,
  type        text check (type in ('image','video')),
  prompt      text,
  output_url  text,
  created_at  timestamptz default now()
);

-- Storage bucket for user uploads (optional, since xAI returns URLs directly)
-- Create a public bucket named "generations" in Supabase Storage dashboard
-- if you want to mirror/cache generated files.
