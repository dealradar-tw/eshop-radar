-- 特價雷達：共用搜尋標籤（使用者新增的標籤，所有人都能搜尋得到）。可重複執行。
-- 網頁只能「送出標籤」和「讀取全部標籤」，不能修改或刪除；管理者在 Supabase 後台刪除不當內容。
create table if not exists public.game_tags (
  id bigint generated always as identity primary key,
  game_key text not null check (game_key ~ '^[0-9]{6}$' or (game_key like 'nso:%' and char_length(game_key) <= 120)),
  tag text not null check (char_length(tag) between 1 and 20),
  device uuid not null,
  created_at timestamptz not null default now(),
  unique (game_key, tag, device)
);
create index if not exists game_tags_key_idx on public.game_tags (game_key);
alter table public.game_tags enable row level security;
revoke all on public.game_tags from anon, authenticated;

-- 送出標籤：同一來源每小時最多 60 個、同一裝置每天最多 30 個
create or replace function public.submit_tag(p_device uuid, p_key text, p_tag text)
returns void
language plpgsql security definer set search_path = public as $$
declare t text := left(btrim(regexp_replace(coalesce(p_tag, ''), '^#+', '')), 20); n int;
begin
  if p_device is null or p_key is null or char_length(t) < 1 then raise exception 'bad input'; end if;
  if not (p_key ~ '^[0-9]{6}$' or (p_key like 'nso:%' and char_length(p_key) <= 120)) then raise exception 'bad key'; end if;
  if t ~ '(https?://|www\.|@)' then raise exception 'no links'; end if;
  if lower(regexp_replace(t, '[\s\-_.,，。、*＊·・~!！?？]', '', 'g')) ~ '幹你娘|幹你媽|幹林|幹您|操你|肏|靠北|靠杯|三小|機掰|雞掰|機歪|幹話連|白癡|白痴|智障|低能|腦殘|賤人|婊子|王八蛋|去死|死全家|死ㄍㄚ|屌|懶叫|懒叫|屁眼|他媽的|他媽|媽的|妈的|草泥馬|肏你|fuck|shit|bitch|asshole|cunt|nigger|nigga|porn|av女優|強暴|强奸|強姦|援交|約砲|炮友|做愛|性愛|成人影片|裸照|支那|鬼子|黑鬼|賤種|殘障|畜生' then raise exception 'bad word'; end if;   -- 不接受網址與信箱
  perform public._rate('tag', 60, interval '1 hour');
  select count(*) into n from public.game_tags where device = p_device and created_at > now() - interval '1 day';
  if n >= 30 then raise exception 'rate limited'; end if;
  insert into public.game_tags (game_key, tag, device) values (p_key, t, p_device) on conflict do nothing;
end $$;
grant execute on function public.submit_tag(uuid, text, text) to anon;

-- 讀取全部標籤：{遊戲編號: [標籤, ...]}，被越多裝置加的排越前面，每款最多 12 個
create or replace function public.get_tags()
returns json
language plpgsql stable security definer set search_path = public as $$
declare r json;
begin
  select coalesce(json_object_agg(game_key, tags), '{}'::json) into r from (
    select game_key, json_agg(tag order by c desc, tag) filter (where rk <= 12) as tags from (
      select game_key, tag, count(*) as c, row_number() over (partition by game_key order by count(*) desc, tag) as rk
      from public.game_tags group by game_key, tag) x
    group by game_key) y;
  return r;
end $$;
grant execute on function public.get_tags() to anon;

-- 管理者通知用：問「有幾個新標籤」，只回數量與最新編號（用同一組 feedback_notify 密鑰）
create or replace function public.admin_tags_new(p_token text, p_since bigint)
returns json
language plpgsql security definer set search_path = public as $$
declare ok boolean; r json;
begin
  perform public._rate('admintags', 30, interval '1 hour');
  select exists (select 1 from public.admin_secret
                 where name = 'feedback_notify' and hash = encode(sha256(convert_to(coalesce(p_token, ''), 'utf8')), 'hex')) into ok;
  if not ok then raise exception 'denied'; end if;
  select json_build_object('n', count(*), 'max_id', coalesce(max(id), p_since)) into r from public.game_tags where id > p_since;
  return r;
end $$;
grant execute on function public.admin_tags_new(text, bigint) to anon;

-- 管理者操作（在 Supabase 後台 SQL Editor 執行）：
-- 查看最近標籤：select id, created_at at time zone 'Asia/Taipei' as 時間, game_key, tag, device from game_tags order by id desc limit 100;
-- 刪除某個標籤：delete from game_tags where tag = '不當內容';
-- 封鎖某裝置的全部標籤：delete from game_tags where device = '裝置編號';
