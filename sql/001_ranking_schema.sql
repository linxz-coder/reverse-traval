CREATE DATABASE IF NOT EXISTS reverse_travel_rankings
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE reverse_travel_rankings;

CREATE TABLE IF NOT EXISTS ranking_profiles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  profile_key VARCHAR(64) NOT NULL,
  profile_name_zh VARCHAR(80) NOT NULL,
  target_audience VARCHAR(32) NOT NULL,
  description_zh VARCHAR(255) DEFAULT NULL,
  filters_json JSON DEFAULT NULL,
  no_surge_rule_json JSON DEFAULT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ranking_profiles_key (profile_key),
  KEY idx_ranking_profiles_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ranking_cities (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  city_name_zh VARCHAR(64) NOT NULL,
  city_name_en VARCHAR(128) DEFAULT NULL,
  province_name_zh VARCHAR(64) DEFAULT NULL,
  country_code CHAR(2) NOT NULL DEFAULT 'CN',
  trip_city_id VARCHAR(32) DEFAULT NULL,
  latitude DECIMAL(10,7) DEFAULT NULL,
  longitude DECIMAL(10,7) DEFAULT NULL,
  is_cache_warmup_enabled TINYINT(1) NOT NULL DEFAULT 1,
  notes VARCHAR(255) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ranking_cities_name_country (city_name_zh, country_code),
  KEY idx_ranking_cities_trip_city_id (trip_city_id),
  KEY idx_ranking_cities_warmup (is_cache_warmup_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hotel_profiles (
  hotel_id VARCHAR(64) NOT NULL,
  trip_hotel_id VARCHAR(64) DEFAULT NULL,
  city_id BIGINT UNSIGNED DEFAULT NULL,
  city_name_zh VARCHAR(64) DEFAULT NULL,
  hotel_name_zh VARCHAR(255) NOT NULL,
  hotel_name_original VARCHAR(255) DEFAULT NULL,
  star_rating DECIMAL(3,1) DEFAULT NULL,
  review_score DECIMAL(3,1) DEFAULT NULL,
  area_name_zh VARCHAR(128) DEFAULT NULL,
  address_zh VARCHAR(255) DEFAULT NULL,
  latitude DECIMAL(10,7) DEFAULT NULL,
  longitude DECIMAL(10,7) DEFAULT NULL,
  detail_url VARCHAR(1024) DEFAULT NULL,
  image_url VARCHAR(1024) DEFAULT NULL,
  tags_json JSON DEFAULT NULL,
  facilities_json JSON DEFAULT NULL,
  last_seen_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (hotel_id),
  KEY idx_hotel_profiles_city (city_id),
  KEY idx_hotel_profiles_trip_hotel_id (trip_hotel_id),
  KEY idx_hotel_profiles_city_name (city_name_zh),
  CONSTRAINT fk_hotel_profiles_city
    FOREIGN KEY (city_id) REFERENCES ranking_cities (id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ranking_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_key CHAR(32) DEFAULT NULL,
  profile_id BIGINT UNSIGNED NOT NULL,
  city_id BIGINT UNSIGNED DEFAULT NULL,
  city_name_zh VARCHAR(64) NOT NULL,
  trip_city_id VARCHAR(32) DEFAULT NULL,
  holiday_code VARCHAR(64) NOT NULL,
  holiday_name_zh VARCHAR(80) NOT NULL,
  check_in_date DATE NOT NULL,
  check_out_date DATE NOT NULL,
  nights SMALLINT UNSIGNED NOT NULL,
  search_mode ENUM('single_city', 'nearby_city') NOT NULL DEFAULT 'single_city',
  source ENUM('prewarm', 'manual', 'admin', 'import') NOT NULL DEFAULT 'prewarm',
  status ENUM('pending', 'running', 'succeeded', 'failed') NOT NULL DEFAULT 'pending',
  search_params_json JSON DEFAULT NULL,
  comparison_windows_json JSON DEFAULT NULL,
  summary_json JSON DEFAULT NULL,
  total_hotels_found INT UNSIGNED NOT NULL DEFAULT 0,
  eligible_hotels_count INT UNSIGNED NOT NULL DEFAULT 0,
  recommended_hotels_count INT UNSIGNED NOT NULL DEFAULT 0,
  max_price_increase_cny DECIMAL(10,2) DEFAULT NULL,
  max_price_increase_pct DECIMAL(8,4) DEFAULT NULL,
  error_message VARCHAR(512) DEFAULT NULL,
  started_at DATETIME DEFAULT NULL,
  completed_at DATETIME DEFAULT NULL,
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ranking_snapshots_key (snapshot_key),
  KEY idx_ranking_snapshots_lookup (city_name_zh, holiday_code, profile_id, generated_at),
  KEY idx_ranking_snapshots_status (status, generated_at),
  KEY idx_ranking_snapshots_city (city_id),
  CONSTRAINT fk_ranking_snapshots_profile
    FOREIGN KEY (profile_id) REFERENCES ranking_profiles (id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT fk_ranking_snapshots_city
    FOREIGN KEY (city_id) REFERENCES ranking_cities (id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ranking_hotels (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_id BIGINT UNSIGNED NOT NULL,
  rank_no INT UNSIGNED NOT NULL,
  hotel_id VARCHAR(64) DEFAULT NULL,
  city_id BIGINT UNSIGNED DEFAULT NULL,
  city_name_zh VARCHAR(64) NOT NULL,
  hotel_name_zh VARCHAR(255) NOT NULL,
  hotel_name_original VARCHAR(255) DEFAULT NULL,
  area_name_zh VARCHAR(128) DEFAULT NULL,
  star_rating DECIMAL(3,1) DEFAULT NULL,
  review_score DECIMAL(3,1) DEFAULT NULL,
  room_name_zh VARCHAR(255) DEFAULT NULL,
  room_name_original VARCHAR(255) DEFAULT NULL,
  holiday_avg_price_cny DECIMAL(10,2) DEFAULT NULL,
  holiday_tax_included_price_cny DECIMAL(10,2) DEFAULT NULL,
  compare_avg_price_cny DECIMAL(10,2) DEFAULT NULL,
  price_diff_cny DECIMAL(10,2) DEFAULT NULL,
  price_diff_pct DECIMAL(8,4) DEFAULT NULL,
  no_surge_status ENUM('not_increased', 'slight_increase', 'increased', 'unknown') NOT NULL DEFAULT 'unknown',
  value_score DECIMAL(8,3) DEFAULT NULL,
  family_score DECIMAL(8,3) DEFAULT NULL,
  star_score DECIMAL(8,3) DEFAULT NULL,
  recommendation_reason_zh TEXT DEFAULT NULL,
  price_comparison_json JSON DEFAULT NULL,
  availability_json JSON DEFAULT NULL,
  tags_json JSON DEFAULT NULL,
  raw_hotel_json JSON DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ranking_hotels_rank (snapshot_id, rank_no),
  UNIQUE KEY uq_ranking_hotels_hotel_room (snapshot_id, hotel_id, room_name_zh),
  KEY idx_ranking_hotels_snapshot_score (snapshot_id, value_score),
  KEY idx_ranking_hotels_city (city_id),
  KEY idx_ranking_hotels_hotel_id (hotel_id),
  CONSTRAINT fk_ranking_hotels_snapshot
    FOREIGN KEY (snapshot_id) REFERENCES ranking_snapshots (id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_ranking_hotels_city
    FOREIGN KEY (city_id) REFERENCES ranking_cities (id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW v_latest_ranking_hotels AS
SELECT
  rs.city_name_zh,
  rs.holiday_code,
  rs.holiday_name_zh,
  rs.check_in_date,
  rs.check_out_date,
  rp.profile_key,
  rp.profile_name_zh,
  rh.rank_no,
  rh.hotel_id,
  rh.hotel_name_zh,
  rh.hotel_name_original,
  rh.area_name_zh,
  rh.star_rating,
  rh.review_score,
  rh.room_name_zh,
  rh.holiday_avg_price_cny,
  rh.compare_avg_price_cny,
  rh.price_diff_cny,
  rh.price_diff_pct,
  rh.no_surge_status,
  rh.recommendation_reason_zh,
  rs.generated_at
FROM ranking_hotels rh
JOIN ranking_snapshots rs ON rs.id = rh.snapshot_id
JOIN ranking_profiles rp ON rp.id = rs.profile_id
WHERE rs.status = 'succeeded'
  AND rs.generated_at = (
    SELECT MAX(rs2.generated_at)
    FROM ranking_snapshots rs2
    WHERE rs2.status = 'succeeded'
      AND rs2.city_name_zh = rs.city_name_zh
      AND rs2.holiday_code = rs.holiday_code
      AND rs2.profile_id = rs.profile_id
  );

INSERT IGNORE INTO ranking_profiles (
  profile_key,
  profile_name_zh,
  target_audience,
  description_zh,
  filters_json,
  no_surge_rule_json
) VALUES
(
  'family_no_surge',
  '亲子酒店不涨价榜',
  'family',
  '适合亲子出行，优先考虑儿童设施、泳池、房型可订和假期不明显涨价。',
  JSON_OBJECT(
    'child_facility_filter', 'yes',
    'pool_filter', 'optional',
    'advanced_filter', 'optional',
    'room_types', JSON_ARRAY('大床房', '双床房'),
    'name_language', 'zh-Hans'
  ),
  JSON_OBJECT(
    'max_increase_cny', 100,
    'max_increase_pct', 0.08,
    'compare_days', 3,
    'tax_included', true
  )
),
(
  'star_no_surge',
  '星级酒店不涨价榜',
  'star',
  '适合品质型出行，优先考虑高星酒店、评分和假期不明显涨价。',
  JSON_OBJECT(
    'advanced_filter', 'yes',
    'child_facility_filter', 'optional',
    'pool_filter', 'optional',
    'room_types', JSON_ARRAY('大床房', '双床房'),
    'name_language', 'zh-Hans'
  ),
  JSON_OBJECT(
    'max_increase_cny', 100,
    'max_increase_pct', 0.08,
    'compare_days', 3,
    'tax_included', true
  )
);
