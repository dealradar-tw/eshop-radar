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
alter table public.sync_log add column if not exists scope text not null default 'sync';
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
    select count(*) into cnt from public.sync_log where ip_hash = iph and scope = 'sync' and ts > now() - interval '1 hour';
    if cnt >= 120 then raise exception 'rate limited'; end if;
    if is_new then
      select count(*) into cnt from public.sync_log where ip_hash = iph and scope = 'sync' and new_device and ts > now() - interval '1 day';
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


-- ═════════════ 評論區與點擊統計 ═════════════
-- 留言公開顯示（暱稱＋內容＋時間）；裝置隨機編號只用來限制頻率、判斷「是不是自己的留言」，不會回傳給其他人。

create table if not exists public.comments (
  id bigint generated always as identity primary key,
  game text not null check (game ~ '^[0-9]{6}$'),
  parent bigint references public.comments(id) on delete cascade,   -- 回覆哪一則（只有兩層：留言 → 回覆）
  device uuid not null,
  nick text not null check (char_length(nick) between 1 and 20),
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamptz not null default now(),
  hidden boolean not null default false,      -- 被檢舉達門檻自動隱藏，或管理者手動隱藏
  n_helpful int not null default 0,
  n_reports int not null default 0
);
create index if not exists comments_game_ts on public.comments (game, created_at desc);
create index if not exists comments_device_ts on public.comments (device, created_at);
create index if not exists comments_parent on public.comments (parent);

create table if not exists public.comment_helpful (
  comment_id bigint not null references public.comments(id) on delete cascade,
  device uuid not null,
  created_at timestamptz not null default now(),
  primary key (comment_id, device)
);

create table if not exists public.comment_reports (
  comment_id bigint not null references public.comments(id) on delete cascade,
  device uuid not null,
  reason text not null check (reason in ('abuse', 'spam', 'other')),   -- 用詞不當／廣告洗版／其他
  created_at timestamptz not null default now(),
  primary key (comment_id, device)
);

-- 點擊統計：每台裝置每天、每款遊戲最多算 1 次。明細只留 2 天（用來去重），累計數字另存。
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

alter table public.comments enable row level security;
alter table public.comment_helpful enable row level security;
alter table public.comment_reports enable row level security;
alter table public.game_views enable row level security;
alter table public.game_stats enable row level security;
revoke all on public.comments, public.comment_helpful, public.comment_reports, public.game_views, public.game_stats from anon, authenticated;

-- 公開彙總（網頁可讀）
create or replace view public.comment_counts as
  select c.game as id, count(*)::int as n
  from public.comments c
  left join public.comments p on p.id = c.parent
  where not c.hidden and (c.parent is null or not p.hidden)
  group by c.game;

create or replace view public.view_counts as
  select id, views::int as n from public.game_stats;

grant select on public.comment_counts, public.view_counts to anon;

-- ───────────── 內部小工具（不開放給網頁） ─────────────
create or replace function public._ip_hash() returns text
language plpgsql stable security definer set search_path = public as $$
declare hdr json; ip text;
begin
  begin
    hdr := nullif(current_setting('request.headers', true), '')::json;
  exception when others then
    hdr := null;
  end;
  ip := btrim(coalesce(hdr->>'cf-connecting-ip', split_part(coalesce(hdr->>'x-forwarded-for', ''), ',', 1), ''));
  if ip = '' then return null; end if;
  return md5('dealradar-v1:' || ip);
end $$;

-- 同一來源在時間窗內某類動作的次數上限
create or replace function public._rate(p_scope text, p_limit int, p_window interval) returns void
language plpgsql security definer set search_path = public as $$
declare iph text; cnt int;
begin
  iph := public._ip_hash();
  if iph is null then return; end if;
  select count(*) into cnt from public.sync_log where ip_hash = iph and scope = p_scope and ts > now() - p_window;
  if cnt >= p_limit then raise exception 'rate limited'; end if;
  insert into public.sync_log (ip_hash, scope) values (iph, p_scope);
end $$;

-- 確保裝置紀錄存在；同一來源每天最多新增 30 個新裝置（防止換編號繞過限制）
create or replace function public._touch_device(p_device uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from public.devices where device = p_device) then
    perform public._rate('newdev', 30, interval '1 day');
  end if;
  insert into public.devices (device) values (p_device)
    on conflict (device) do update set last_sync = now();
end $$;

revoke all on function public._ip_hash() from public, anon, authenticated;
revoke all on function public._rate(text, int, interval) from public, anon, authenticated;
revoke all on function public._touch_device(uuid) from public, anon, authenticated;

-- ───────────── 點擊統計 ─────────────
create or replace function public.log_view(p_device uuid, p_id text) returns void
language plpgsql security definer set search_path = public as $$
declare n int; today date := (now() at time zone 'Asia/Taipei')::date;
begin
  if p_device is null or p_id is null or p_id !~ '^[0-9]{6}$' then raise exception 'bad input'; end if;
  perform public._rate('view', 600, interval '1 hour');
  perform public._touch_device(p_device);
  select count(*) into n from public.game_views where device = p_device and day = today;
  if n >= 300 then return; end if;                       -- 單一裝置一天最多計 300 款
  insert into public.game_views (device, id, day) values (p_device, p_id, today) on conflict do nothing;
  get diagnostics n = row_count;
  if n > 0 then
    insert into public.game_stats (id, views) values (p_id, 1)
      on conflict (id) do update set views = public.game_stats.views + 1;
  end if;
  if random() < 0.02 then
    delete from public.game_views where day < today - 2;
  end if;
end $$;

-- ───────────── 評論區 ─────────────
-- 讀取：只回傳未隱藏的留言；不回傳裝置編號
create or replace function public.list_comments(p_game text, p_device uuid default null)
returns table (id bigint, parent bigint, nick text, body text, created_at timestamptz, n_helpful int, mine boolean, helped boolean)
language sql stable security definer set search_path = public as $$
  select c.id, c.parent, c.nick, c.body, c.created_at, c.n_helpful,
         (p_device is not null and c.device = p_device) as mine,
         (p_device is not null and exists (select 1 from public.comment_helpful h where h.comment_id = c.id and h.device = p_device)) as helped
  from public.comments c
  left join public.comments p on p.id = c.parent
  where c.game = p_game and not c.hidden and (c.parent is null or not p.hidden)
  order by c.created_at desc
  limit 300;
$$;

-- 留言／回覆。限制：暱稱 1～20 字、內容 1～500 字、不能放網址、暱稱不能冒充管理者；
-- 同一裝置 10 分鐘最多 5 則、一天最多 30 則，同一來源一小時最多 30 則，同樣內容一天只能發一次。
create or replace function public.post_comment(p_device uuid, p_game text, p_parent bigint, p_nick text, p_body text)
returns bigint
language plpgsql security definer set search_path = public as $$
declare
  v_nick text := btrim(coalesce(p_nick, ''));
  v_body text := btrim(coalesce(p_body, ''));
  par public.comments%rowtype;
  v_top bigint;
  cnt int;
  new_id bigint;
begin
  if p_device is null then raise exception 'bad device'; end if;
  if p_game is null or p_game !~ '^[0-9]{6}$' then raise exception 'bad game'; end if;
  if char_length(v_nick) not between 1 and 20 then raise exception 'bad nick'; end if;
  if v_nick ~* '(管理|admin|客服|官方|站長|moderator)' then raise exception 'reserved nick'; end if;
  if char_length(v_body) not between 1 and 500 then raise exception 'bad body'; end if;
  if v_body ~* '(https?://|www\.|[a-z0-9-]+\.(com|net|org|tw|cc|xyz|top|io)\y)' then raise exception 'no links'; end if;

  if p_parent is not null then
    select * into par from public.comments where id = p_parent;
    if not found or par.hidden or par.game <> p_game then raise exception 'bad parent'; end if;
    v_top := coalesce(par.parent, par.id);               -- 回覆「回覆」時，掛在最上層那則底下
  end if;

  perform public._rate('comment', 30, interval '1 hour');
  perform public._touch_device(p_device);
  select count(*) into cnt from public.comments where device = p_device and created_at > now() - interval '10 minutes';
  if cnt >= 5 then raise exception 'slow down'; end if;
  select count(*) into cnt from public.comments where device = p_device and created_at > now() - interval '1 day';
  if cnt >= 30 then raise exception 'daily limit'; end if;
  if exists (select 1 from public.comments where device = p_device and comments.body = v_body and created_at > now() - interval '1 day') then
    raise exception 'duplicate';
  end if;

  insert into public.comments (game, parent, device, nick, body) values (p_game, v_top, p_device, v_nick, v_body)
    returning comments.id into new_id;
  return new_id;
end $$;

-- 有幫助：每個裝置對每則留言只能點 1 次，不能點自己的
create or replace function public.mark_helpful(p_device uuid, p_comment bigint) returns int
language plpgsql security definer set search_path = public as $$
declare c public.comments%rowtype; n int; total int;
begin
  if p_device is null or p_comment is null then raise exception 'bad input'; end if;
  select * into c from public.comments where id = p_comment and not hidden;
  if not found then raise exception 'not found'; end if;
  if c.device = p_device then raise exception 'own comment'; end if;
  perform public._rate('react', 120, interval '1 hour');
  perform public._touch_device(p_device);
  insert into public.comment_helpful (comment_id, device) values (p_comment, p_device) on conflict do nothing;
  get diagnostics n = row_count;
  if n > 0 then
    update public.comments set n_helpful = n_helpful + 1 where id = p_comment returning n_helpful into total;
  else
    select n_helpful into total from public.comments where id = p_comment;
  end if;
  return total;
end $$;

-- 檢舉：每個裝置對每則留言只能檢舉 1 次，達 3 人先自動隱藏，等管理者處理
create or replace function public.report_comment(p_device uuid, p_comment bigint, p_reason text) returns boolean
language plpgsql security definer set search_path = public as $$
declare c public.comments%rowtype; total int; is_hidden boolean;
begin
  if p_device is null or p_comment is null then raise exception 'bad input'; end if;
  if p_reason is null or p_reason not in ('abuse', 'spam', 'other') then raise exception 'bad reason'; end if;
  select * into c from public.comments where id = p_comment;
  if not found then raise exception 'not found'; end if;
  if c.device = p_device then raise exception 'own comment'; end if;
  perform public._rate('report', 30, interval '1 hour');
  perform public._touch_device(p_device);
  insert into public.comment_reports (comment_id, device, reason) values (p_comment, p_device, p_reason) on conflict do nothing;
  select count(*)::int into total from public.comment_reports where comment_id = p_comment;
  update public.comments set n_reports = total, hidden = (hidden or total >= 3) where id = p_comment returning hidden into is_hidden;
  return is_hidden;
end $$;

-- 刪除自己的留言（連同別人對它的回覆）
create or replace function public.delete_my_comment(p_device uuid, p_comment bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  if p_device is null or p_comment is null then raise exception 'bad input'; end if;
  delete from public.comments where id = p_comment and device = p_device;
end $$;

revoke all on function public.log_view(uuid, text) from public;
revoke all on function public.list_comments(text, uuid) from public;
revoke all on function public.post_comment(uuid, text, bigint, text, text) from public;
revoke all on function public.mark_helpful(uuid, bigint) from public;
revoke all on function public.report_comment(uuid, bigint, text) from public;
revoke all on function public.delete_my_comment(uuid, bigint) from public;
grant execute on function public.log_view(uuid, text) to anon;
grant execute on function public.list_comments(text, uuid) to anon;
grant execute on function public.post_comment(uuid, text, bigint, text, text) to anon;
grant execute on function public.mark_helpful(uuid, bigint) to anon;
grant execute on function public.report_comment(uuid, bigint, text) to anon;
grant execute on function public.delete_my_comment(uuid, bigint) to anon;

-- ───────────── 管理者用：檢舉清單（只有 Supabase 後台看得到，網頁不能讀） ─────────────
create or replace view public.admin_reports as
  select c.id, c.game, c.parent, c.nick, c.body, c.created_at, c.hidden, c.n_reports, c.n_helpful,
         (select string_agg(r.reason, '、' order by r.created_at) from public.comment_reports r where r.comment_id = c.id) as reasons
  from public.comments c
  where c.n_reports > 0
  order by c.hidden desc, c.n_reports desc, c.created_at desc;
revoke all on public.admin_reports from anon, authenticated;

-- ───────────── 使用者自行刪除自己的排行資料 ─────────────
create or replace function public.delete_my_marks(p_device uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_device is null then raise exception 'bad device'; end if;
  delete from public.marks where device = p_device;
  delete from public.comments where device = p_device;          -- 自己的留言（連同別人對它的回覆）
  delete from public.comment_helpful where device = p_device;
  delete from public.comment_reports where device = p_device;
  delete from public.game_views where device = p_device;
  delete from public.devices where device = p_device;
end
$$;

revoke all on function public.delete_my_marks(uuid) from public;
grant execute on function public.delete_my_marks(uuid) to anon;
