USE reverse_travel_rankings;

CREATE TABLE IF NOT EXISTS hotel_price_observations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  observation_key CHAR(32) DEFAULT NULL,
  hotel_id VARCHAR(64) DEFAULT NULL,
  trip_hotel_id VARCHAR(64) DEFAULT NULL,
  city_id BIGINT UNSIGNED DEFAULT NULL,
  city_name_zh VARCHAR(64) NOT NULL,
  hotel_name_zh VARCHAR(255) NOT NULL,
  hotel_name_original VARCHAR(255) DEFAULT NULL,
  area_name_zh VARCHAR(128) DEFAULT NULL,
  holiday_code VARCHAR(64) DEFAULT NULL,
  price_role ENUM('holiday', 'comparison', 'other') NOT NULL DEFAULT 'other',
  comparison_label VARCHAR(64) DEFAULT NULL,
  price_date DATE NOT NULL,
  check_in_date DATE NOT NULL,
  check_out_date DATE NOT NULL,
  nights SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  room_name_zh VARCHAR(255) DEFAULT NULL,
  room_name_original VARCHAR(255) DEFAULT NULL,
  currency_code CHAR(3) NOT NULL DEFAULT 'CNY',
  base_price_cny DECIMAL(10,2) DEFAULT NULL,
  tax_fee_cny DECIMAL(10,2) DEFAULT NULL,
  tax_included_price_cny DECIMAL(10,2) DEFAULT NULL,
  avg_nightly_tax_included_price_cny DECIMAL(10,2) DEFAULT NULL,
  breakfast_label_zh VARCHAR(128) DEFAULT NULL,
  cancel_policy_zh VARCHAR(255) DEFAULT NULL,
  is_available TINYINT(1) NOT NULL DEFAULT 1,
  is_advanced TINYINT(1) DEFAULT NULL,
  has_pool TINYINT(1) DEFAULT NULL,
  has_child_facility TINYINT(1) DEFAULT NULL,
  source ENUM('dom', 'api', 'cache', 'manual', 'import') NOT NULL DEFAULT 'api',
  search_job_key CHAR(32) DEFAULT NULL,
  ranking_snapshot_id BIGINT UNSIGNED DEFAULT NULL,
  observed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  raw_price_json JSON DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_hotel_price_observation_key (observation_key),
  KEY idx_hotel_prices_lookup (city_name_zh, holiday_code, price_role, check_in_date, check_out_date, is_available),
  KEY idx_hotel_prices_city_date (city_name_zh, price_date, is_available, tax_included_price_cny),
  KEY idx_hotel_prices_hotel_date (hotel_id, price_date, observed_at),
  KEY idx_hotel_prices_trip_hotel_date (trip_hotel_id, price_date, observed_at),
  KEY idx_hotel_prices_features (is_advanced, has_pool, has_child_facility),
  KEY idx_hotel_prices_snapshot (ranking_snapshot_id),
  KEY idx_hotel_prices_job (search_job_key),
  KEY idx_hotel_prices_observed (observed_at),
  CONSTRAINT fk_hotel_price_observations_hotel
    FOREIGN KEY (hotel_id) REFERENCES hotel_profiles (hotel_id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_hotel_price_observations_city
    FOREIGN KEY (city_id) REFERENCES ranking_cities (id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_hotel_price_observations_snapshot
    FOREIGN KEY (ranking_snapshot_id) REFERENCES ranking_snapshots (id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW v_latest_hotel_prices AS
SELECT
  id,
  hotel_id,
  trip_hotel_id,
  city_name_zh,
  hotel_name_zh,
  hotel_name_original,
  area_name_zh,
  price_date,
  check_in_date,
  check_out_date,
  nights,
  room_name_zh,
  room_name_original,
  currency_code,
  base_price_cny,
  tax_fee_cny,
  tax_included_price_cny,
  avg_nightly_tax_included_price_cny,
  breakfast_label_zh,
  cancel_policy_zh,
  is_available,
  source,
  search_job_key,
  ranking_snapshot_id,
  observed_at
FROM (
  SELECT
    hpo.*,
    ROW_NUMBER() OVER (
      PARTITION BY
        city_name_zh,
        COALESCE(hotel_id, trip_hotel_id, hotel_name_zh),
        price_date,
        COALESCE(room_name_zh, '')
      ORDER BY observed_at DESC, id DESC
    ) AS row_num
  FROM hotel_price_observations hpo
) ranked_prices
WHERE row_num = 1;
