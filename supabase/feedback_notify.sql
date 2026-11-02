-- 意見回饋通知用：讓自動更新能問「有幾則新回饋」，只回數量與最新編號，不回內容。可重複執行。
create table if not exists public.admin_secret (name text primary key, hash text not null);
alter table public.admin_secret enable row level security;
revoke all on public.admin_secret from anon, authenticated;
insert into public.admin_secret (name, hash) values ('feedback_notify', '61291ae26aae94ec3e4550ea34078179ea400886780ec0971aaaefa164ca4716')
  on conflict (name) do update set hash = excluded.hash;

create or replace function public.admin_feedback_new(p_token text, p_since bigint)
returns json
language plpgsql security definer set search_path = public as $$
declare ok boolean; r json;
begin
  perform public._rate('adminfb', 30, interval '1 hour');
  select exists (select 1 from public.admin_secret
                 where name = 'feedback_notify' and hash = encode(sha256(convert_to(coalesce(p_token, ''), 'utf8')), 'hex')) into ok;
  if not ok then raise exception 'denied'; end if;
  select json_build_object('n', count(*), 'max_id', coalesce(max(id), p_since)) into r from public.feedback where id > p_since;
  return r;
end $$;
grant execute on function public.admin_feedback_new(text, bigint) to anon;
