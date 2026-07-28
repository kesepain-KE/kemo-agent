# 感知验收标准

```powershell
python -m tests.template_tests.sense --target global_sense/<name>
```

## 必须成立

- `sense.json` 能被感知清单解析器读取；
- 更新脚本提供同步零参数 `update()` 或 `main()`；
- 成功执行后，清单声明的 Markdown 出口非空，时间和健康状态有效；
- Prompt 来源注册器能发现并注入该出口。

## 不限制

感知可以小到一个时间值，也可以连接传感器、浏览器、数据库、API、媒体处理管线或完整工程。
清单入口只是适配层，不规定采集方式、缓存格式、内部进程、文件数量和目录层级。
