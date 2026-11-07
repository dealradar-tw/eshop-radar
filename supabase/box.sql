-- 特價雷達：跨裝置同步（同步碼）。可重複執行。
-- 同步碼只在使用者的裝置上，資料庫只存它的 SHA-256 雜湊，所以管理者也看不到同步碼；網頁只能透過下面三個函式存取，不能直接讀寫表。
create table if not exists public.sync_box (
  code_hash text primary key,
  data jsonb not null,
  ver int not null default 1,
  updated_at timestamptz not null default now()
);
alter table public.sync_box enable row level security;
revoke all on public.sync_box from anon, authenticated;

create or replace function public._box_hash(p_code text) returns text
language plpgsql immutable set search_path = public as $$
begin
  if p_code is null or p_code !~ '^[A-Za-z0-9]{20,64}$' then raise exception 'bad code'; end if;
  return encode(sha256(convert_to('box:' || upper(p_code), 'utf8')), 'hex');
end $$;
revoke all on function public._box_hash(text) from public, anon, authenticated;

-- 讀取：回傳 {data, ver}；沒有資料就回傳 null
create or replace function public.box_pull(p_code text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare h text := public._box_hash(p_code); r record;
begin
  perform public._rate('box', 300, interval '1 hour');
  select data, ver into r from public.sync_box where code_hash = h;
  if not found then return null; end if;
  return jsonb_build_object('data', r.data, 'ver', r.ver);
end $$;

-- 寫入：p_ver 是你上次讀到的版本（第一次傳 0）。版本對不上就回傳 {ok:false}，網頁會重新讀取合併後再寫。
create or replace function public.box_push(p_code text, p_data jsonb, p_ver int) returns jsonb
language plpgsql security definer set search_path = public as $$
declare h text := public._box_hash(p_code); cur int; n int;
begin
  perform public._rate('box', 300, interval '1 hour');
  if p_data is null or jsonb_typeof(p_data) <> 'object' or octet_length(p_data::text) > 400000 then raise exception 'bad data'; end if;
  select ver into cur from public.sync_box where code_hash = h for update;
  if not found then
    perform public._rate('boxnew', 5, interval '1 day');              -- 同一來源每天最多建立 5 組新的同步碼
    insert into public.sync_box (code_hash, data, ver) values (h, p_data, 1);
    return jsonb_build_object('ok', true, 'ver', 1);
  end if;
  if cur <> p_ver then return jsonb_build_object('ok', false, 'ver', cur); end if;
  update public.sync_box set data = p_data, ver = cur + 1, updated_at = now() where code_hash = h;
  return jsonb_build_object('ok', true, 'ver', cur + 1);
end $$;

-- 刪除雲端資料
create or replace function public.box_delete(p_code text) returns void
language plpgsql security definer set search_path = public as $$
declare h text := public._box_hash(p_code);
begin
  perform public._rate('box', 300, interval '1 hour');
  delete from public.sync_box where code_hash = h;
end $$;

grant execute on function public.box_pull(text) to anon;
grant execute on function public.box_push(text, jsonb, int) to anon;
grant execute on function public.box_delete(text) to anon;

-- 管理者可以看有幾組同步資料（看不到內容與同步碼）：
-- select count(*), max(updated_at) from sync_box;
