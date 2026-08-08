"""API 路由包：把 dashboard.py 的 HTTP 端点按业务域拆分。

设计约定：
- 每个子模块定义 `router = APIRouter()`，由 dashboard 统一 include；
- 端点函数**运行时**从 dashboard 取共享状态（`from dashboard import pool,
  _api_cache, ...`），保持 dashboard 为唯一状态持有者——测试对
  `dashboard.pool` / `dashboard._api_cache` / `dashboard.get_settings` 的
  monkeypatch 继续生效；
- 业务域：keys（Key 池/健康/异常/用量同步）、usage（用量聚合/趋势/预测）、
  research（任务看板/重试/内置文档）、logs（请求日志/审计）、services
  （MCP/搜索代理）、admin（设置/自启/备份恢复）、update（GitHub 更新）。
"""
