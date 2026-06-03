-- 将已有表从字符串业务主键改为 BIGINT 自增技术主键。
-- 适用于当前 quantitative_trad.stock_basic 已有 5057 条正确数据的情况。
-- 执行前建议先备份数据库或至少备份这两张表。

USE quantitative_trad;

-- 1) stock_basic：保留现有数据，新增 id 作为自增主键，ts_code 改为唯一业务键。
ALTER TABLE stock_basic
  DROP PRIMARY KEY,
  ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键' FIRST,
  ADD PRIMARY KEY (id),
  ADD UNIQUE KEY uq_stock_basic_ts_code (ts_code);

-- 2) stock_pools：保留现有数据，新增 id 作为自增主键，code 改为唯一业务键。
ALTER TABLE stock_pools
  DROP PRIMARY KEY,
  ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键' FIRST,
  ADD PRIMARY KEY (id),
  ADD UNIQUE KEY uq_stock_pools_code (code);
