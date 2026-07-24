---
type: component
project: kemo-agent
domain: run
module: run-memory_migrate
layer: L2
scope: project
status: active
summary: run/memory_migrate.py — 记忆 v1→v2 一次性迁移工具
source: run/memory_migrate.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, run, memory, migrate, 迁移]
---
# run/memory_migrate.py — 记忆 v1→v2 一次性迁移工具

`E:\code\kemo-agent\run\memory_migrate.py` → `migrate_user_memory()`

## 概览

将旧版数组式 JSON 记忆存储（schema v1）迁移为文件型存储（schema v2）。迁移完成后 v2 引擎可直接使用。

## 迁移目标

- **v1（旧）**：`users/<user>/improve/<tier>/data.json` 中的 JSON 数组
- **v2（新）**：`users/<user>/improve/<tier>/<filename>.md` 文件 + `data.json` 索引

## 迁移流程

1. **检测**：检查 `improve/storage.json` 中 `schema_version` 是否为 v2，已 v2 则跳过
2. **读取**：逐档读取旧 `data.json` 数组
3. **过滤**：跳过空内容；拒绝包含敏感凭据的条目
4. **去重**：检测全层级文件名冲突，有冲突则报错中止
5. **写入暂存区**：在 `users/.improve-v2-<uuid>/` 下构建 v2 结构
6. **校验**：验证索引完整性、跨层同名检查、文件计数
7. **备份**：将原目录复制为 `improve_backup_v1_<timestamp>_<id>`
8. **替换**：原子替换（原目录移动 → 暂存区替换）

## 函数签名

```python
def migrate_user_memory(
    root: Path, user: str,
    *, dry_run=False, backup=True, now=None
) -> MigrationReport
```

### MigrationReport

```python
@dataclass(frozen=True, slots=True)
class MigrationReport:
    user: str
    migrated: bool        # 是否实际执行了迁移
    already_v2: bool      # 是否已是 v2（跳过）
    files: int            # 迁移的文件数
    rejected_sensitive: int  # 因敏感凭据拒绝的条目数
    backup: str           # 备份路径
    conflicts: tuple[str, ...]  # 文件名冲突列表
```

## CLI 使用

```bash
python -m run.memory_migrate --user kesepain
python -m run.memory_migrate --all
python -m run.memory_migrate --all --dry-run
```

## 注意事项

- 迁移前自动备份原目录，可通过 `--no-backup` 跳过
- 拒绝迁移包含疑似敏感凭据（API Key / Token / 密码等）的记忆条目
- 迁移是单向操作，不可反向回退
- 支持 `--dry-run` 预览迁移结果

## 相关笔记

- [[run-memory]]（v2 文件型记忆引擎）
- [[run-总览]]
- 原理-记忆升级权重
