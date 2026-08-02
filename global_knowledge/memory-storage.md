# 记忆 SQLite 存储

四档记忆以每用户独立的 `users/<user>/improve/memory.sqlite3` 为唯一权威存储。旧式 Markdown 碎片、三层 `data.json`、`storage.json`、`.memory_operations.json` 和 `important_view.json` 不再参与读取，也不会自动导入。`memory_temporary_important.md` 仍是位于用户根目录的可重建热视图，不是权威数据库或第五个生命周期层。

## 为什么独立建库

记忆与历史采用两个数据库：`improve/memory.sqlite3` 和 `history/history.sqlite3`。二者的写入频率、保留周期和维护任务不同，分库可避免记忆加权与聊天归档争用同一写锁，也便于单独备份、诊断和重建。

数据库启用 WAL、`synchronous=NORMAL`、外键、5 秒 busy timeout 和显式写事务。连接与 schema 集中在 `run/memory_store.py`，业务规则集中在 `run/memory_sqlite.py`。生产代码只通过条目级接口读取或修改记忆，不再提供模拟旧 `data.json` 的 `load_index`、`write_index`、层级目录或正文文件路径接口，外部代码不得直接拼 SQL 修改生命周期状态。

## 表结构

| 表 | 用途 | 关键约束 |
|---|---|---|
| `memory_meta` | schema 与派生视图元数据 | `key` 主键 |
| `memory_fragments` | 四档正文和生命周期 | `filename_key` 全局唯一；tier 枚举；权重非负；永久层无 `expires_at` |
| `memory_weight_events` | 用户历史证据导致的加权记录 | `(fragment_id, evidence_date)` 主键，数据库强制每日最多一次 |
| `memory_operations` | 批量提取幂等结果 | `operation_id` 主键，重复批次只重放结果 |
| `memory_important_sources` | 临时重要热视图引用 | 每个临时源最多一条，并保存内容摘要用于失效判断 |

`filename` 保留 `.md` 后缀只是稳定的逻辑身份和 UI 展示合同，不表示磁盘上存在对应 Markdown。跨层同名由数据库唯一约束拒绝，晋升直接更新同一行的 tier，不复制正文文件。

## 生命周期与事务

- 新的非明确候选进入 `seven_days`；用户明确要求长期记住的候选可直接进入 `permanent`。
- 同名临时候选更新正文后，生命周期到期时间不滑动；用户历史证据在当天第一次命中时写入 `memory_weight_events` 并使权重加一。
- 到期且达到阈值时，在一个事务内更新 tier、重置权重和加权事件、写入新的固定到期时间；未达阈值则事务化删除。
- 语义融合会在同一事务内更新目标正文和生命周期、删除来源；任何一步失败都会回滚。
- 永久层不累计权重且无到期时间，普通非明确候选不能覆盖永久正文。

Prompt 注入、Web 浏览、`memory_manage` 搜索和用户主动查看均为只读操作，不写加权事件。只有保存、手动压缩、Token 超限压缩等用户对话历史整理管线能提供临时记忆加权证据。

## 临时重要热视图

数据流固定为：

```text
用户对话历史 → 临时三层表行 → memory_temporary_important.md
```

后台整理临时三层时禁止把 `memory_temporary_important.md` 作为输入证据。热视图发布时，`memory_important_sources` 记录来源行和内容摘要；来源被修改、删除或晋升后，旧热视图立即视为失效，普通临时层恢复注入，等待下次巡检重建。主动查看记忆时仍可读取该文件，但查看本身不加权。

## 模板、升级与旧格式

`template/user/improve/` 只包含 `.gitkeep`，不得提交预生成的二进制数据库。`user_create.py` 在真实用户目录落地后初始化 schema；更新器也只初始化缺失数据库，不导入或删除部署者的旧文件。当前源代码旧记忆已被抛弃；需要保留旧部署数据时，应在升级前自行导出，不能假定框架会自动迁移。

## 备份与维护

- 最稳妥的备份方式是停止写入后复制整个 `improve/`，或在运行中使用 SQLite backup API。
- WAL 模式运行时不能只复制 `memory.sqlite3` 而忽略尚未 checkpoint 的 WAL。
- 不提交 `memory.sqlite3`、`-wal` 或 `-shm` 到模板或 Git。
- 完整性检查使用 `MemoryStore.integrity_issues()`；不要通过手工编辑数据库修复生产数据。
