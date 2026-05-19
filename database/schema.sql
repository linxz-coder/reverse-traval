CREATE DATABASE IF NOT EXISTS reverse_travel_archive
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE reverse_travel_archive;

CREATE TABLE IF NOT EXISTS hotels (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  external_source VARCHAR(64) NOT NULL DEFAULT 'trip.com',
  external_hotel_id VARCHAR(64) NOT NULL,
  city_name VARCHAR(128) NOT NULL,
  area_name VARCHAR(255) NULL,
  hotel_name_zh_cn VARCHAR(255) NOT NULL,
  hotel_name_original VARCHAR(255) NULL,
  hotel_name_source VARCHAR(128) NULL,
  is_advanced TINYINT(1) NULL,
  has_pool TINYINT(1) NULL,
  has_child_facility TINYINT(1) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_hotel_source_id (external_source, external_hotel_id),
  KEY idx_hotels_city_area (city_name, area_name),
  KEY idx_hotels_name (hotel_name_zh_cn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hotel_name_aliases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  hotel_id BIGINT UNSIGNED NOT NULL,
  alias_name VARCHAR(255) NOT NULL,
  alias_type ENUM('simplified','traditional','english','platform','manual','other') NOT NULL DEFAULT 'other',
  source VARCHAR(128) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_hotel_alias (hotel_id, alias_name, alias_type),
  KEY idx_alias_name (alias_name),
  CONSTRAINT fk_alias_hotel
    FOREIGN KEY (hotel_id) REFERENCES hotels(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS list_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_uuid CHAR(36) NOT NULL,
  source_payload_hash CHAR(64) NOT NULL,
  city_name VARCHAR(128) NOT NULL,
  holiday_name VARCHAR(128) NOT NULL,
  holiday_code VARCHAR(128) NULL,
  check_in DATE NOT NULL,
  check_out DATE NOT NULL,
  nights INT UNSIGNED NOT NULL,
  data_source VARCHAR(128) NOT NULL DEFAULT 'Trip.com',
  source_path VARCHAR(512) NULL,
  source_cache_created_texts TEXT NULL,
  filter_advanced ENUM('all','yes','no') NOT NULL DEFAULT 'yes',
  filter_pool ENUM('all','yes','no') NOT NULL DEFAULT 'all',
  filter_child_facility ENUM('all','yes','no') NOT NULL DEFAULT 'all',
  min_price_cny DECIMAL(10,2) NULL,
  max_price_cny DECIMAL(10,2) NULL,
  price_basis VARCHAR(255) NOT NULL DEFAULT '端午每晚含税均价，对比未来一月非法定假期代表时段每晚含税均价',
  notes TEXT NULL,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_snapshot_uuid (snapshot_uuid),
  UNIQUE KEY uniq_source_payload_hash (source_payload_hash),
  KEY idx_snapshots_city_holiday (city_name, holiday_name, check_in, check_out),
  KEY idx_snapshots_filters (filter_advanced, filter_pool, filter_child_facility)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS list_entries (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_id BIGINT UNSIGNED NOT NULL,
  list_type ENUM('star_no_rise','family_no_rise','discount_star') NOT NULL,
  rank_no INT UNSIGNED NOT NULL,
  hotel_id BIGINT UNSIGNED NOT NULL,
  filter_advanced ENUM('all','yes','no') NOT NULL DEFAULT 'yes',
  filter_pool ENUM('all','yes','no') NOT NULL DEFAULT 'all',
  filter_child_facility ENUM('all','yes','no') NOT NULL DEFAULT 'all',
  is_advanced TINYINT(1) NULL,
  has_pool TINYINT(1) NULL,
  has_child_facility TINYINT(1) NULL,
  holiday_avg_nightly_tax_total_cny DECIMAL(10,2) NOT NULL,
  comparison_avg_nightly_tax_total_cny DECIMAL(10,2) NULL,
  price_diff_nightly_cny DECIMAL(10,2) NOT NULL,
  room_type_label VARCHAR(64) NULL,
  recommendation_reason TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_snapshot_list_rank (snapshot_id, list_type, rank_no),
  KEY idx_entries_list_type (list_type),
  KEY idx_entries_hotel (hotel_id),
  KEY idx_entries_filters (filter_advanced, filter_pool, filter_child_facility),
  CONSTRAINT fk_entries_snapshot
    FOREIGN KEY (snapshot_id) REFERENCES list_snapshots(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_entries_hotel
    FOREIGN KEY (hotel_id) REFERENCES hotels(id)
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS price_observations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_id BIGINT UNSIGNED NOT NULL,
  hotel_id BIGINT UNSIGNED NOT NULL,
  list_entry_id BIGINT UNSIGNED NULL,
  check_in DATE NOT NULL,
  check_out DATE NOT NULL,
  nights INT UNSIGNED NOT NULL,
  room_type_label VARCHAR(64) NULL,
  holiday_avg_nightly_tax_total_cny DECIMAL(10,2) NOT NULL,
  comparison_avg_nightly_tax_total_cny DECIMAL(10,2) NULL,
  price_diff_nightly_cny DECIMAL(10,2) NOT NULL,
  comparison_sample_count INT UNSIGNED NULL,
  observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_observation_entry (list_entry_id),
  KEY idx_price_hotel_date (hotel_id, check_in, check_out),
  KEY idx_price_snapshot (snapshot_id),
  CONSTRAINT fk_price_snapshot
    FOREIGN KEY (snapshot_id) REFERENCES list_snapshots(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_price_hotel
    FOREIGN KEY (hotel_id) REFERENCES hotels(id)
    ON DELETE RESTRICT,
  CONSTRAINT fk_price_entry
    FOREIGN KEY (list_entry_id) REFERENCES list_entries(id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS name_review_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  external_source VARCHAR(64) NOT NULL DEFAULT 'trip.com',
  external_hotel_id VARCHAR(64) NULL,
  city_name VARCHAR(128) NULL,
  raw_name VARCHAR(255) NOT NULL,
  suggested_name VARCHAR(255) NULL,
  reason VARCHAR(255) NOT NULL,
  status ENUM('pending','resolved','ignored') NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_review_status (status),
  KEY idx_review_city (city_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS shared_cache_entries (
  cache_namespace VARCHAR(64) NOT NULL,
  cache_key_hash CHAR(64) NOT NULL,
  cache_key_json LONGTEXT NOT NULL,
  payload_json LONGTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  source_node VARCHAR(128) NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (cache_namespace, cache_key_hash),
  KEY idx_shared_cache_expires (expires_at),
  KEY idx_shared_cache_namespace_created (cache_namespace, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW v_list_entries_detail AS
SELECT
  s.id AS snapshot_id,
  s.snapshot_uuid,
  s.city_name,
  s.holiday_name,
  s.check_in,
  s.check_out,
  e.list_type,
  e.rank_no,
  h.external_source,
  h.external_hotel_id,
  h.hotel_name_zh_cn,
  h.area_name,
  e.filter_advanced,
  e.filter_pool,
  e.filter_child_facility,
  e.is_advanced,
  e.has_pool,
  e.has_child_facility,
  e.holiday_avg_nightly_tax_total_cny,
  e.comparison_avg_nightly_tax_total_cny,
  e.price_diff_nightly_cny,
  e.room_type_label,
  e.recommendation_reason,
  s.generated_at
FROM list_entries e
JOIN list_snapshots s ON s.id = e.snapshot_id
JOIN hotels h ON h.id = e.hotel_id;
