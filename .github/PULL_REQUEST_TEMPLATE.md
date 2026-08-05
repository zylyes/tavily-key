---
name: Pull Request
about: 提交代码变更
title: ""
labels: ""
assignees: ""

---

## 变更摘要

用一两句话说明本次变更做了什么。

关联 issue（如有）：`Closes #<issue 编号>`

## 动机

为什么需要这个变更？解决了什么问题？

## 测试验证

描述你做的验证（请尽量完整勾选）：

- [ ] 已运行 `python -m pytest`，全部测试通过
- [ ] 已手动验证相关 CLI 命令（如 `python app/cli.py list`、`python app/cli.py health`）
- [ ] 已启动 Web 控制台验证（`./scripts/run_dashboard.sh` 或 `scripts\run_dashboard.bat`）
- [ ] 涉及 MCP 服务时，已验证 sse / streamable-http / stdio 传输方式

## 检查清单

- [ ] 代码风格符合 [CONTRIBUTING.md](../../CONTRIBUTING.md) 的要求（Python 3、遵循 `app/` 现有模块结构）
- [ ] 未提交任何机密信息（`config.json`、`keys.txt`、`tavily_keys.db`、真实 API key / 令牌等）
- [ ] 涉及文档/界面文案时，已使用中文
- [ ] 涉及行为变更时，已同步更新 README、`docs/` 或 CHANGELOG（如适用）
