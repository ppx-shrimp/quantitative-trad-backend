-- Migration: add audit fields to all existing MySQL tables
-- Database: quantitative_trad
-- Purpose: 补齐创建时间、更新时间、创建人、更新人等审计字段
-- Execute this script after sql/mysql_schema.sql has been applied.

USE quantitative_trad;

-- 股票池定义表：已有 created_at / updated_at，补充 created_by / updated_by
ALTER TABLE stock_pools
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 股票池成员表：已有 created_at / updated_at，补充 created_by / updated_by
ALTER TABLE stock_pool_members
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 股票 K 线数据表：已有 created_at / updated_at，补充 created_by / updated_by
ALTER TABLE stock_klines
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- K 线同步日志表：已有 created_at，补充 updated_at / created_by / updated_by
ALTER TABLE kline_sync_logs
  ADD COLUMN updated_at VARCHAR(64) NOT NULL DEFAULT '' COMMENT '更新时间 ISO 字符串' AFTER created_at,
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 股票基础特征表：已有 created_at / updated_at，补充 created_by / updated_by
ALTER TABLE stock_features
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 模拟账户表：已有 created_at / updated_at，补充 created_by / updated_by
ALTER TABLE paper_accounts
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 模拟当前持仓表：已有 opened_at / updated_at，补充 created_at / created_by / updated_by
ALTER TABLE paper_positions
  ADD COLUMN created_at VARCHAR(64) NOT NULL DEFAULT '' COMMENT '创建时间 ISO 字符串' AFTER opened_at,
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 模拟订单记录表：已有 created_at，补充 updated_at / created_by / updated_by
ALTER TABLE paper_orders
  ADD COLUMN updated_at VARCHAR(64) NOT NULL DEFAULT '' COMMENT '更新时间 ISO 字符串' AFTER created_at,
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 模拟资金流水表：已有 created_at，补充 updated_at / created_by / updated_by
ALTER TABLE paper_cash_flows
  ADD COLUMN updated_at VARCHAR(64) NOT NULL DEFAULT '' COMMENT '更新时间 ISO 字符串' AFTER created_at,
  ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '创建人' AFTER updated_at,
  ADD COLUMN updated_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '更新人' AFTER created_by;

-- 对历史数据补齐缺失更新时间，避免新增字段为空字符串
UPDATE kline_sync_logs
SET updated_at = created_at
WHERE updated_at = '';

UPDATE paper_positions
SET created_at = opened_at
WHERE created_at = '';

UPDATE paper_orders
SET updated_at = created_at
WHERE updated_at = '';

UPDATE paper_cash_flows
SET updated_at = created_at
WHERE updated_at = '';
