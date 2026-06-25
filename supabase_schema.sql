-- Run this in your Supabase SQL editor

create table if not exists users (
  id               bigserial primary key,
  username         text unique not null,
  password         text not null,
  email            text,
  birthday         text,
  is_adult         boolean default false,
  signup_ip        text,
  is_admin         boolean default false,
  api_key1         text unique,
  api_key2         text unique,
  images_today     int default 0,
  videos_today     int default 0,
  last_reset       timestamptz default now(),
  picture_credits  int default 0,
  video_credits    int default 0,
  created_at       timestamptz default now()
);

-- Run these if upgrading an existing database:
alter table users add column if not exists email text;
alter table users add column if not exists is_adult boolean default false;
alter table users add column if not exists signup_ip text;
alter table users add column if not exists is_admin boolean default false;
update users set is_adult = true where birthday is not null and birthday <> '' and birthday::date <= (current_date - interval '18 years');

-- Allow guest princess generations in history (optional)
alter table generations alter column user_id drop not null;

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
