-- 累計瀏覽次數（取代第三方計數服務 abacus）
-- 在 Supabase 的 SQL Editor 貼上整份後按 Run；可重複執行，不會清掉已累計的數字。
-- 只存一個總數，不記錄任何個人或裝置資料；同一來源每小時最多計 120 次，防止被灌水。

create table if not exists public.site_counter (
  k text primary key,
  n bigint not null default 0
);
alter table public.site_counter enable row level security;   -- 不開任何讀寫政策：網頁只能透過下面的函式存取
revoke all on public.site_counter from anon, authenticated;

insert into public.site_counter (k, n) values ('visits', 0) on conflict (k) do nothing;

-- p_hit = true：算一次瀏覽並回傳新的總數；false：只讀取
create or replace function public.site_visit(p_hit boolean default false) returns bigint
language plpgsql security definer set search_path = public as $$
declare v bigint;
begin
  if p_hit then
    perform public._rate('visit', 120, interval '1 hour');
    update public.site_counter set n = n + 1 where k = 'visits' returning n into v;
  else
    select n into v from public.site_counter where k = 'visits';
  end if;
  return coalesce(v, 0);
end $$;

revoke all on function public.site_visit(boolean) from public;
grant execute on function public.site_visit(boolean) to anon;

-- 想把舊的累計數字接續上去（例如原本約 200 多次），在 SQL Editor 單獨執行下面這行，數字自己改：
-- update public.site_counter set n = 230 where k = 'visits';
