# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、补税、滞纳金、处罚和证据完整性和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `src/archive_domain.py`：归档资料（档案盒、卷宗、借阅、修补待办）的数据与输入校验。
- `src/archive_rules.py`：归档与借阅规则（结案装盒、唯一盒、盒容量、借阅冲突、逾期、缺页修补）。
- `src/archive_repository.py`：归档表的SQLite事务与查询。
- `src/archive_service.py`：归档用例编排，装盒事件写入既有稽查审计时间线。
- `static/index.html`：最小演示页面。
- `static/archive.html`：归档管理页面（装盒、借阅、逾期红标、归还与缺页修补）。
- `tests/`：完整流程、规则计算、失败场景和归档借阅测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8326
```

默认端口为`8326`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 归档与借阅接口

归档页面：`GET /archive`。归档写操作（登记盒、装盒、借阅、归还、补齐）需`reviewer`或`admin`角色，`inspector`只读。

- `GET /api/archive/stats`：盒、卷宗、借阅、修补统计。
- `POST /api/archive/boxes`：登记唯一档案盒，`{"data":{"box_code":"BOX-001","shelf_location":"A-3-2","capacity_pages":500}}`。
- `GET /api/archive/boxes`：档案盒列表（含已用/剩余页数）。
- `POST /api/records/{id}/archive`：结案案件装盒，`{"data":{"box_code":"...","title":"...","pages":120}}`；未结案、重复装盒、超容量均返回409。
- `GET /api/archive/dossiers?status=shelved|on_loan|in_repair`：卷宗列表。
- `POST /api/archive/dossiers/{id}/borrow`：复核人员凭用途和归还日借阅，`{"data":{"purpose":"复议调卷","due_date":"2026-10-01"}}`；在借或修补中返回409，归还日不得早于当天。
- `GET /api/archive/loans?active=1`：借阅列表，活跃借阅超过归还日时`overdue=true`，页面红色标出。
- `POST /api/archive/dossiers/{id}/return`：归还，`{"data":{"missing_pages":true,"note":"缺12-15页"}}`；缺页自动生成修补待办并将卷宗置为修补中。
- `GET /api/archive/repairs?pending=1`：修补待办列表。
- `POST /api/archive/dossiers/{id}/repair`：登记补齐，`{"data":{"note":"已补齐四页"}}`，卷宗恢复在架后方可重新借阅。

业务规则：案件结案后才能装入唯一档案盒（一盒可装多卷，盒内页数累计不得超过容量）；同一卷宗归还前不能再次借阅；逾期未还在借阅列表和页面标出；归还发现缺页先转修补待办，补齐后才能重新借阅。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
