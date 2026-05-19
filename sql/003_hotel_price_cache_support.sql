USE reverse_travel_rankings;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'holiday_code'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN holiday_code VARCHAR(64) DEFAULT NULL AFTER area_name_zh',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'price_role'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN price_role ENUM(''holiday'', ''comparison'', ''other'') NOT NULL DEFAULT ''other'' AFTER holiday_code',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'comparison_label'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN comparison_label VARCHAR(64) DEFAULT NULL AFTER price_role',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'is_advanced'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN is_advanced TINYINT(1) DEFAULT NULL AFTER is_available',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'has_pool'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN has_pool TINYINT(1) DEFAULT NULL AFTER is_advanced',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND column_name = 'has_child_facility'
);
SET @col_sql := IF(
  @col_exists = 0,
  'ALTER TABLE hotel_price_observations ADD COLUMN has_child_facility TINYINT(1) DEFAULT NULL AFTER has_pool',
  'SELECT 1'
);
PREPARE col_stmt FROM @col_sql;
EXECUTE col_stmt;
DEALLOCATE PREPARE col_stmt;

SET @idx_exists := (
  SELECT COUNT(*)
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND index_name = 'idx_hotel_prices_lookup'
);
SET @idx_sql := IF(
  @idx_exists = 0,
  'CREATE INDEX idx_hotel_prices_lookup ON hotel_price_observations (city_name_zh, holiday_code, price_role, check_in_date, check_out_date, is_available)',
  'SELECT 1'
);
PREPARE idx_stmt FROM @idx_sql;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @idx_exists := (
  SELECT COUNT(*)
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'hotel_price_observations'
    AND index_name = 'idx_hotel_prices_features'
);
SET @idx_sql := IF(
  @idx_exists = 0,
  'CREATE INDEX idx_hotel_prices_features ON hotel_price_observations (is_advanced, has_pool, has_child_facility)',
  'SELECT 1'
);
PREPARE idx_stmt FROM @idx_sql;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;
