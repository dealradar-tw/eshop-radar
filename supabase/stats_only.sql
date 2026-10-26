-- 只建「點擊統計」（不含評論區）。可重複執行。
alter table public.sync_log add column if not exists scope text not null default 'sync';

create table if not exists public.game_views (
  device uuid not null,
  id text not null check (id ~ '^[0-9]{6}$'),
  day date not null,
  primary key (device, id, day)
);
create table if not exists public.game_stats (
  id text primary key check (id ~ '^[0-9]{6}$'),
  views bigint not null default 0
);
alter table public.game_views enable row level security;
alter table public.game_stats enable row level security;
revoke all on public.game_views, public.game_stats from anon, authenticated;

create or replace view public.view_counts as
  select id, views::int as n from public.game_stats;
grant select on public.view_counts to anon;

create or replace function public._rate(p_scope text, p_limit int, p_window interval) returns void
language plpgsql security definer set search_path = public as $$
declare hdr json; ip text; iph text; cnt int;
begin
  begin hdr := nullif(current_setting('request.headers', true), '')::json; exception when others then hdr := null; end;
  ip := btrim(coalesce(hdr->>'cf-connecting-ip', split_part(coalesce(hdr->>'x-forwarded-for', ''), ',', 1), ''));
  if ip = '' then return; end if;
  iph := md5('dealradar-v1:' || ip);
  select count(*) into cnt from public.sync_log where ip_hash = iph and scope = p_scope and ts > now() - p_window;
  if cnt >= p_limit then raise exception 'rate limited'; end if;
  insert into public.sync_log (ip_hash, scope) values (iph, p_scope);
end $$;
revoke all on function public._rate(text, int, interval) from public, anon, authenticated;

create or replace function public.log_view(p_device uuid, p_id text) returns void
language plpgsql security definer set search_path = public as $$
declare n int; today date := (now() at time zone 'Asia/Taipei')::date;
begin
  if p_device is null or p_id is null or p_id !~ '^[0-9]{6}$' then raise exception 'bad input'; end if;
  perform public._rate('view', 600, interval '1 hour');
  if not exists (select 1 from public.devices where device = p_device) then
    perform public._rate('newdev', 30, interval '1 day');
    insert into public.devices (device) values (p_device) on conflict do nothing;
  end if;
  select count(*) into n from public.game_views where device = p_device and day = today;
  if n >= 300 then return; end if;
  insert into public.game_views (device, id, day) values (p_device, p_id, today) on conflict do nothing;
  get diagnostics n = row_count;
  if n > 0 then
    insert into public.game_stats (id, views) values (p_id, 1)
      on conflict (id) do update set views = public.game_stats.views + 1;
  end if;
  if random() < 0.02 then delete from public.game_views where day < today - 2; end if;
end $$;
revoke all on function public.log_view(uuid, text) from public;
grant execute on function public.log_view(uuid, text) to anon;
