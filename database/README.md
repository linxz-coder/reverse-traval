# 本地 MySQL 榜单归档库

这套库只用于本地归档，不依赖线上服务器。以后换服务器或部署方式，历史榜单数据仍保留在本机 MySQL 里。

## 表设计

- `hotels`：酒店主表，正式展示名统一存 `hotel_name_zh_cn`，必须是中文简体。
- `hotel_name_aliases`：酒店别名，保存平台名、原始名、人工确认名等。
- `list_snapshots`：每次生成榜单的快照，不覆盖旧数据。
- `list_entries`：某次榜单中的排名、榜单类型、筛选项、推荐理由和当时价格。
- `price_observations`：价格观测记录，保存端午每晚含税价、平日均价和每晚差额。
- `name_review_queue`：名称无法确认简体时的待审核队列。
- `shared_cache_entries`：可选共享缓存表。启用后会保存基础搜索和行政区补充结果，方便服务器端和 tunnel 端共享缓存。
- `v_list_entries_detail`：给 Navicat 浏览用的视图，已经把快照、酒店、榜单明细和筛选项关联好。

筛选项会同时存在两层：

- `list_snapshots.filter_advanced/filter_pool/filter_child_facility`：这次快照的总体筛选口径。
- `list_entries.filter_advanced/filter_pool/filter_child_facility`：每张榜单的具体筛选口径。亲子榜默认为 `yes/yes/yes`，普通星级榜和降价榜默认为 `yes/all/all`。

## 初始化数据库

本地 MySQL 运行后执行：

```bash
cd /Users/linxiaozhong/development/reverse-travel-good-choice
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD='你的密码' \
  python3 scripts/import_xhs_lists_to_mysql.py --create-schema --replace
```

不想导入、只检查数据：

```bash
python3 scripts/import_xhs_lists_to_mysql.py --dry-run
```

只导入某个城市：

```bash
MYSQL_USER=root MYSQL_PASSWORD='你的密码' \
  python3 scripts/import_xhs_lists_to_mysql.py --create-schema exports/xhs_guangzhou_duanwu_2026
```

## 可选共享缓存

默认仍然优先使用应用本机 `.cache` 目录，不依赖 MySQL。需要让服务器端和 Mac/tunnel 共享搜索缓存时，再给两个环境配置同一个 MySQL：

```bash
REVERSE_TRAVEL_SHARED_CACHE_MYSQL=1
REVERSE_TRAVEL_SHARED_CACHE_HOST=127.0.0.1
REVERSE_TRAVEL_SHARED_CACHE_PORT=3306
REVERSE_TRAVEL_SHARED_CACHE_DATABASE=reverse_travel_archive
REVERSE_TRAVEL_SHARED_CACHE_USER=reverse_travel
REVERSE_TRAVEL_SHARED_CACHE_PASSWORD='你的密码'
REVERSE_TRAVEL_NODE_NAME=mac-tunnel
```

本地 tunnel 会自动读取项目目录下的 `.env.shared-cache`，可以从 `.env.shared-cache.example` 复制后修改。Linux 服务器会读取 `/etc/reverse-traval/shared-cache.env`，模板在 `deploy/linux/shared-cache.env.example`。

读取顺序是：内存缓存 -> 本机 `.cache` 文件 -> MySQL 共享缓存 -> 实时查询。MySQL 连不上会自动跳过 60 秒，不会阻塞用户搜索。coverage 行政区补充缓存也支持 7 天旧缓存预览：先显示旧补充结果，再后台刷新最新数据。

## Navicat 连接

推荐新建一个只用于本地归档的 MySQL 用户：

```sql
CREATE USER IF NOT EXISTS 'reverse_travel'@'localhost' IDENTIFIED BY '换成你的强密码';
GRANT ALL PRIVILEGES ON reverse_travel_archive.* TO 'reverse_travel'@'localhost';
FLUSH PRIVILEGES;
```

Navicat 新建 MySQL 连接：

- 连接名：`反向旅游本地归档`
- 主机：`127.0.0.1`
- 端口：`3306`
- 用户名：`reverse_travel`
- 密码：上面设置的密码
- 数据库：`reverse_travel_archive`

不要把 MySQL 端口暴露到公网。以后如果要远程看库，用 SSH Tunnel。

常用查看 SQL：

```sql
SELECT *
FROM v_list_entries_detail
WHERE city_name = '广州'
ORDER BY generated_at DESC, list_type, rank_no;
```

筛出亲子榜：

```sql
SELECT city_name, holiday_name, list_type, rank_no, hotel_name_zh_cn,
       holiday_avg_nightly_tax_total_cny, price_diff_nightly_cny,
       filter_advanced, filter_pool, filter_child_facility
FROM v_list_entries_detail
WHERE list_type = 'family_no_rise'
ORDER BY generated_at DESC, city_name, rank_no;
```

## 备份

```bash
MYSQL_USER=root MYSQL_PASSWORD='你的密码' scripts/backup_local_mysql.sh
```

默认备份到：

```text
~/reverse_travel_mysql_backups/
```

恢复示例：

```bash
gunzip -c ~/reverse_travel_mysql_backups/reverse_travel_archive_YYYYMMDD_HHMMSS.sql.gz \
  | MYSQL_USER=root MYSQL_PASSWORD='你的密码' mysql reverse_travel_archive
```
