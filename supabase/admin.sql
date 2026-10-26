-- 管理者用（只在 Supabase 後台 SQL Editor 執行）。
-- 1) 看檢舉：最新的在前
select * from admin_reports limit 50;
-- 2) 直接刪除某則留言（含其下回覆）：把 123 換成留言 id
-- delete from comments where id = 123;
-- 3) 只隱藏、不刪除
-- update comments set hidden = true where id = 123;
-- 4) 誤檢舉，恢復顯示
-- update comments set hidden = false, n_reports = 0 where id = 123;
-- delete from comment_reports where comment_id = 123;
