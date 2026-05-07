# reverse-travel-good-choice

反向旅游好选择：输入所在城市、法定假期和酒店条件后，对比假期与未来非法定假期的酒店含税均价，推荐没有明显涨价的附近城市酒店和旅游片区。

## 功能

- 单城市酒店反向旅游推荐
- 全球城市查询，Trip.com 能识别的目的地都可以试
- 全球城市推荐旅游区域，优先从 Trip.com 位置/商圈文本识别片区
- 附近城市推荐，例如深圳可推荐汕尾、惠州、广州、东莞
- 大床房 / 双床房按每晚含税均价对比
- 高级酒店、游泳池、儿童设施筛选
- 查询过程可视化，长查询时显示当前抓取和对比阶段
- 24 小时共享缓存；用户可选择只看缓存或发起新搜索
- 全国主要城市缓存预热任务
- PDF 报告生成脚本

## 启动

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 app.py
```

默认服务地址：

```text
http://127.0.0.1:5012
```

## 测试

```bash
python3 -m pytest -q
```

## 本机公网上线

项目包含 macOS `launchd` 配置：

- `deploy/launchd/com.linxz.reverse-traval.app.plist`
- `deploy/launchd/com.linxz.reverse-traval.tunnel.plist`

当前部署方式是本机 Flask 服务加 Cloudflare Quick Tunnel。公网地址由 `cloudflared` 生成，日志位置：

```text
.cache/cloudflared.err.log
```

## 说明

酒店数据来自 Trip.com 页面与接口结果。实时搜索可能需要数分钟；同条件查询会写入本地缓存，便于其他用户直接复用。
