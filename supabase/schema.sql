-- 特價雷達：人氣排行（關注／購買）統計用資料表，含防灌票保護
-- 只存「裝置隨機編號 + 遊戲編號 + 種類」，不收任何個資。
-- 這份檔案可以重複執行（已建立的東西會略過或更新），升級時整份貼上執行即可。

-- ───────────── 資料表 ─────────────
create table if not exists public.marks (
  device uuid not null,
  id text not null check (id ~ '^[0-9]{6}$'),
  kind text not null check (kind in ('fav', 'buy')),
  created_at timestamptz not null default now(),
  primary key (device, id, kind)
);

-- 每個裝置一列：用來限制異常大量的裝置
create table if not exists public.devices (
  device uuid primary key,
  first_seen timestamptz not null default now(),
  last_sync timestamptz not null default now(),
  n_marks int not null default 0
);

-- 同步紀錄：只存來源 IP 的雜湊值（不存原始 IP），用來限制同一來源的頻率，保留 2 天
create table if not exists public.sync_log (
  id bigint generated always as identity primary key,
  ip_hash text not null,
  ts timestamptz not null default now(),
  new_device boolean not null default false
);
create index if not exists sync_log_ip_ts on public.sync_log (ip_hash, ts);

-- 三張表都開啟列級安全且不建任何政策：網頁不能直接讀寫
alter table public.marks enable row level security;
alter table public.devices enable row level security;
alter table public.sync_log enable row level security;
revoke all on public.marks, public.devices, public.sync_log from anon, authenticated;

-- 舊資料補上裝置紀錄
insert into public.devices (device, n_marks)
  select device, count(*)::int from public.marks group by device
  on conflict (device) do nothing;

-- ───────────── 排行彙總 ─────────────
-- 每款遊戲、每種類的人數；單一裝置標記超過 800 筆的視為異常，不計入
create or replace view public.mark_counts as
  select m.id, m.kind, count(*)::int as n
  from public.marks m
  join public.devices d on d.device = m.device
  where d.n_marks <= 800
  group by m.id, m.kind;

-- ───────────── 寫入函式（網頁唯一的寫入入口） ─────────────
-- 整份覆蓋「這個裝置」的關注／購買清單，可重複執行。限制：
--   每種清單最多 500 筆；
--   同一來源每小時最多 120 次同步；
--   同一來源每天最多新增 20 個新裝置。
create or replace function public.sync_marks(p_device uuid, p_kind text, p_ids text[])
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  ids text[];
  hdr json;
  ip text;
  iph text;
  is_new boolean;
  cnt int;
  total int;
begin
  if p_kind not in ('fav', 'buy') then raise exception 'bad kind'; end if;
  if p_device is null then raise exception 'bad device'; end if;

  select coalesce(array_agg(distinct x), '{}') into ids
    from unnest(coalesce(p_ids, '{}')) x where x ~ '^[0-9]{6}$';
  if cardinality(ids) > 500 then raise exception 'too many'; end if;

  -- 來源 IP：只取雜湊，不存原始值
  begin
    hdr := nullif(current_setting('request.headers', true), '')::json;
  exception when others then
    hdr := null;
  end;
  ip := btrim(coalesce(hdr->>'cf-connecting-ip', split_part(coalesce(hdr->>'x-forwarded-for', ''), ',', 1), ''));
  if ip <> '' then iph := md5('dealradar-v1:' || ip); end if;

  select not exists (select 1 from public.devices where device = p_device) into is_new;

  if iph is not null then
    select count(*) into cnt from public.sync_log where ip_hash = iph and ts > now() - interval '1 hour';
    if cnt >= 120 then raise exception 'rate limited'; end if;
    if is_new then
      select count(*) into cnt from public.sync_log where ip_hash = iph and new_device and ts > now() - interval '1 day';
      if cnt >= 20 then raise exception 'too many new devices'; end if;
    end if;
    insert into public.sync_log (ip_hash, new_device) values (iph, is_new);
  end if;

  insert into public.devices (device) values (p_device)
    on conflict (device) do update set last_sync = now();

  delete from public.marks where device = p_device and kind = p_kind and id <> all(ids);
  insert into public.marks (device, id, kind)
    select p_device, x, p_kind from unnest(ids) x
    on conflict do nothing;

  select count(*)::int into total from public.marks where device = p_device;
  update public.devices set n_marks = total where device = p_device;

  -- 偶爾清理：同步紀錄只留 2 天；180 天沒有同步的裝置連同其標記一起刪除
  if random() < 0.02 then
    delete from public.sync_log where ts < now() - interval '2 days';
    delete from public.marks where device in (select device from public.devices where last_sync < now() - interval '180 days');
    delete from public.devices where last_sync < now() - interval '180 days';
  end if;
end
$$;

revoke all on function public.sync_marks(uuid, text, text[]) from public;
grant execute on function public.sync_marks(uuid, text, text[]) to anon;
grant select on public.mark_counts to anon;
