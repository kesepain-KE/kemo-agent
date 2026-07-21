# get_current_time

获取当前时间。默认以北京时间为基准，不依赖操作系统时区设置。

## 使用原则

1. **涉及当前时间的判断前先调用**：回答“现在几点”“今天日期”“多久后到期”“明天或若干天后”等问题前，先获取当前时间，不依赖对话时间戳。
2. **默认北京时间**：无参数调用始终返回 `Asia/Shanghai` 的北京时间，无论服务部署在哪个操作系统或系统时区。
3. **其他时区直接查询**：东京、纽约、伦敦等地区使用 `target_timezone` 的 IANA 时区名，不自行做固定时差加减；夏令时由时区数据库处理。
4. **时间差使用 Unix 格式**：需要程序化计算时间差时使用 `format=unix`。Unix 时间戳表示绝对瞬间，所以同一次调用中的 `utc`、`local` 和 `target` 数值相同。

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `target_timezone` | string | 空 | 额外查询的 IANA 时区名，如 `Asia/Tokyo`、`America/New_York`、`Europe/London` |
| `format` | string | `iso` | `iso`=ISO 8601、`unix`=秒级时间戳、`date`=当地日期、`time`=当地时刻 |

## 返回字段

| 字段 | 说明 |
|------|------|
| `utc` | 当前 UTC 时间，使用请求的格式 |
| `local` | 当前北京时间，固定使用 `Asia/Shanghai` |
| `iana_timezone` | 固定为 `Asia/Shanghai` |
| `utc_offset` | 当前北京时间偏移，通常为 `+0800` |
| `format` | 实际使用的输出格式 |
| `target` | 目标时区时间，仅传入 `target_timezone` 时返回 |
| `target_timezone` | 规范化后的目标 IANA 时区名 |
| `target_offset` | 目标时区当前偏移，会正确反映夏令时 |

## 示例

### 默认北京时间

输入：`{}`

```json
{
  "utc": "2026-07-21T07:30:00+00:00",
  "local": "2026-07-21T15:30:00+08:00",
  "iana_timezone": "Asia/Shanghai",
  "utc_offset": "+0800",
  "format": "iso"
}
```

### 查询东京时间

输入：`{"target_timezone":"Asia/Tokyo"}`

```json
{
  "utc": "2026-07-21T07:30:00+00:00",
  "local": "2026-07-21T15:30:00+08:00",
  "iana_timezone": "Asia/Shanghai",
  "utc_offset": "+0800",
  "format": "iso",
  "target": "2026-07-21T16:30:00+09:00",
  "target_timezone": "Asia/Tokyo",
  "target_offset": "+0900"
}
```

### 获取 Unix 时间戳

输入：`{"target_timezone":"Asia/Tokyo","format":"unix"}`

```json
{
  "utc": "1784619000",
  "local": "1784619000",
  "iana_timezone": "Asia/Shanghai",
  "utc_offset": "+0800",
  "format": "unix",
  "target": "1784619000",
  "target_timezone": "Asia/Tokyo",
  "target_offset": "+0900"
}
```

## Tool

```json
{
  "name": "get_current_time",
  "description": "获取当前时间，默认返回北京时间。支持查询任意 IANA 时区以及 ISO、Unix、日期和时刻格式；涉及当前时间判断时应先调用本工具。",
  "input_schema": {
    "type": "object",
    "properties": {
      "target_timezone": {
        "type": "string",
        "description": "额外查询的 IANA 时区名，如 Asia/Tokyo、America/New_York；空值表示仅返回 UTC 和北京时间"
      },
      "format": {
        "type": "string",
        "enum": ["iso", "unix", "date", "time"],
        "default": "iso",
        "description": "输出格式：iso=ISO 8601、unix=秒级时间戳、date=当地日期 YYYY-MM-DD、time=当地时刻 HH:MM:SS"
      }
    },
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
