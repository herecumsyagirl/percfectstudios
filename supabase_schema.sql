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
alter table users add column if not exists display_name text;
alter table users add column if not exists is_adult boolean default false;
alter table users add column if not exists signup_ip text;
alter table users add column if not exists is_admin boolean default false;
alter table users add column if not exists stripe_customer_id text;
alter table users add column if not exists social_twitter text;
alter table users add column if not exists social_instagram text;
alter table users add column if not exists social_tiktok text;
alter table users add column if not exists social_youtube text;
alter table users add column if not exists social_website text;
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

-- Promo / offer codes (free credits or limited-use campaigns)
create table if not exists promo_codes (
  id                 bigserial primary key,
  code               text unique not null,
  description        text,
  image_credits      int default 0,
  video_credits      int default 0,
  max_uses           int,
  uses_count         int default 0,
  max_uses_per_user  int default 1,
  expires_at         timestamptz,
  active             boolean default true,
  created_at         timestamptz default now()
);

create table if not exists promo_redemptions (
  id            bigserial primary key,
  user_id       bigint references users(id) on delete cascade,
  promo_code_id bigint references promo_codes(id) on delete cascade,
  redeemed_at   timestamptz default now(),
  unique(user_id, promo_code_id)
);

-- Example launch codes (change or delete in production)
insert into promo_codes (code, description, image_credits, video_credits, max_uses, active)
values
  ('WELCOME10', 'Welcome bonus — 10 free images', 10, 6, null, true),
  ('PRINCESS25', 'Princess launch — 25 images + 15 video seconds', 25, 15, 500, true)
on conflict (code) do nothing;

-- Storage bucket for user uploads (optional, since xAI returns URLs directly)
-- Create a public bucket named "generations" in Supabase Storage dashboard
-- if you want to mirror/cache generated files.
