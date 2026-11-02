-- 特價雷達：意見回饋（只有管理者能讀，網頁只能「送出」）。可重複執行。
create table if not exists public.feedback (
  id bigint generated always as identity primary key,
  device uuid not null,
  kind text not null check (kind in ('bug', 'idea', 'data', 'other')),
  body text not null check (char_length(body) between 5 and 1000),
  contact text check (contact is null or char_length(contact) <= 100),
  page text,
  created_at timestamptz not null default now()
);
alter table public.feedback enable row level security;
revoke all on public.feedback from anon, authenticated;

create or replace function public.submit_feedback(p_device uuid, p_kind text, p_body text, p_contact text, p_page text)
returns void
language plpgsql security definer set search_path = public as $$
declare n int; b text := btrim(coalesce(p_body, ''));
begin
  if p_device is null or p_kind not in ('bug', 'idea', 'data', 'other') then raise exception 'bad input'; end if;
  if char_length(b) < 5 or char_length(b) > 1000 then raise exception 'bad length'; end if;
  perform public._rate('feedback', 10, interval '1 day');          -- 同一來源每天最多 10 則
  select count(*) into n from public.feedback where device = p_device and created_at > now() - interval '1 day';
  if n >= 5 then raise exception 'rate limited'; end if;            -- 同一裝置每天最多 5 則
  insert into public.feedback (device, kind, body, contact, page)
    values (p_device, p_kind, b, nullif(left(btrim(coalesce(p_contact, '')), 100), ''), left(p_page, 200));
end $$;
grant execute on function public.submit_feedback(uuid, text, text, text, text) to anon;

-- 管理者查看（在 Supabase 後台 SQL Editor 執行）：
-- select id, created_at at time zone 'Asia/Taipei' as 時間, kind, body, contact, page from feedback order by id desc limit 50;
