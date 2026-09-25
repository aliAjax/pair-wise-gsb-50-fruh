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
- `src/archive_domain.py`：归档资料数据类型与输入校验（档案盒、卷宗、借阅、修补）。
- `src/archive_rules.py`：借阅规则（盒容量、卷宗状态转换、逾期判定、角色权限）。
- `src/archive_repository.py`：归档SQLite表与事务（盒号唯一、每案一卷、在借唯一）。
- `src/archive_service.py`：归档用例编排，归档动作写入案件审计时间线。
- `static/index.html`：最小演示页面。
- `static/archive.html`：归档与借阅页面，逾期卷红色标出。
- `tests/`：完整流程、规则计算和失败场景测试。

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

归档页面位于`/archive`。归档动作（装盒、借阅、归还、修补）会写入对应案件的审计时间线。

- `GET /api/archive/boxes`：档案盒列表，含已用页数和卷数。
- `POST /api/archive/boxes`：建盒，`{"data":{"box_no":"BOX-001","shelf_location":"A-3-2","capacity_pages":500}}`，盒号唯一。
- `GET /api/archive/dossiers`：卷宗列表，可带`status`和`overdue=1`参数，含在借信息和逾期标记。
- `GET /api/archive/dossiers/{id}`：卷宗详情。
- `POST /api/archive/dossiers`：案件装盒，`{"data":{"record_id":1,"box_id":1,"page_count":120}}`。仅`closed`案件可归档，每案唯一一卷，盒内页数不得超过容量。
- `POST /api/archive/dossiers/{id}/actions/borrow`：借卷，`{"data":{"purpose":"复核抽查","due_date":"2026-10-01"}}`。需`reviewer`角色，归还日不能早于今天，同一卷归还前不能再借。
- `POST /api/archive/dossiers/{id}/actions/return`：还卷，`{"data":{"missing_pages":0}}`。缺页大于0时自动生成修补待办，卷宗转修补中。
- `GET /api/archive/loans`：借阅记录，可带`active=1`和`overdue=1`参数。
- `GET /api/archive/repairs`：修补待办，可带`status=pending`参数。
- `POST /api/archive/repairs/{id}/actions/complete`：补齐完成，`{"data":{"note":"缺页已补齐"}}`，卷宗恢复可借。

归档角色：`archivist`（建盒、装盒、修补）、`reviewer`（借卷、还卷）、`admin`（全部）；其余已知角色只读。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
