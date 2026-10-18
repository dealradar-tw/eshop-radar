-- 特價雷達：人氣排行（關注／購買）統計用資料表
-- 只存「裝置隨機編號 + 遊戲編號 + 種類」，不收任何個資。

create table if not exists public.marks (
  device uuid not null,
  id text not null check (id ~ '^[0-9]{6}$'),
  kind text not null check (kind in ('fav', 'buy')),
  created_at timestamptz not null default now(),
  primary key (device, id, kind)
);

-- 開啟列級安全且不建任何政策：網頁不能直接讀寫這張表
alter table public.marks enable row level security;

-- 排行用的彙總（每款遊戲、每種類的人數）
create or replace view public.mark_counts as
  select id, kind, count(*)::int as n from public.marks group by id, kind;

-- 網頁只能透過這個函式更新「這個裝置」的關注／購買清單（整份覆蓋，可重複執行）
create or replace function public.sync_marks(p_device uuid, p_kind text, p_ids text[])
returns void
language plpgsql
security definer
set search_path = public
as $$
declare ids text[];
begin
  if p_kind not in ('fav', 'buy') then raise exception 'bad kind'; end if;
  select coalesce(array_agg(distinct x), '{}') into ids from unnest(p_ids) x where x ~ '^[0-9]{6}$';
  if cardinality(ids) > 3000 then raise exception 'too many'; end if;
  delete from public.marks where device = p_device and kind = p_kind and id <> all(ids);
  insert into public.marks (device, id, kind) select p_device, x, p_kind from unnest(ids) x on conflict do nothing;
end
$$;

revoke all on function public.sync_marks(uuid, text, text[]) from public;
grant execute on function public.sync_marks(uuid, text, text[]) to anon;
grant select on public.mark_counts to anon;
