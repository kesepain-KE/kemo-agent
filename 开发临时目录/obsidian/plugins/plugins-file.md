---
type: component
project: kemo-agent
domain: plugins
module: plugins-file
layer: L2
scope: project
status: active
summary: plugins/file/ — 文件操作工具集（新增 exists/hash、read max_bytes 保护、tail 反向扫描、copy/move 目录、search 黑名单）
source: plugins/file/tool.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, tool, 文件操作, max_bytes, exists, hash]
---
# plugins/file/ — 文件操作工具集

`E:\code\kemo-agent\plugins\file\`

## 功能

通过 action 参数选择 15 种文件操作：

| action | 功能 |
|--------|------|
| read | 读取整个文件（新增 max_bytes 保护） |
| read_range | 按行读取文件片段（tail 反向扫描优化） |
| write | 覆盖写入 |
| append | 追加写入 |
| edit | 精确编辑（insert/replace_line/replace_range/replace_text） |
| list_dir | 列出目录内容 |
| tree_dir | 目录树 |
| stat | 文件/目录信息 |
| search | 搜索文件名/内容/代码定义（新增黑名单过滤） |
| copy | 复制文件（新增目录支持） |
| move | 移动/重命名文件（新增目录支持） |
| make_dir | 创建目录 |
| delete | 删除文件 |
| **exists** | 检查文件/目录是否存在（新增） |
| **hash** | 计算文件哈希值（新增） |

## 工具定义

- name: `file`
- 无沙箱限制，可直接操作任意路径
- entrypoint: `tool.py:run`

## 优化详情（2026-07-21）

- **read max_bytes**：读取大文件时可指定最大字节数，防止内存溢出
- **read_range tail**：反向扫描实现，不再全量读取
- **search 黑名单**：支持排除特定文件名/目录
- **copy/move 目录**：从小文件操作扩展到支持目录级操作
- **exists/hash**：新增文件存在检查和哈希计算

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]