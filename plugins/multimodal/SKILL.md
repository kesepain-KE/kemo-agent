# multimodal

如果需要当前主模型自己结合上下文接收已取得的媒体，使用 `attach_media` 装载到当前 Run
的下一次请求；本工具仍用于专用模型分析、转写与生成，不替代原生主模型媒体输入。

处理当前一轮已登记的媒体、显式本地媒体路径，或调用 Kemo 专用模型生成媒体。附件优先使用当前 Run 说明中的 `asset_id`；当用户或工具结果给出了明确路径时，也可通过 `paths` 直接提交本地媒体。

## 模式边界

- `provider.type=chat` 只保证 `analyze_image`；其他动作必须明确提示改用 Kemo 协议。
- `provider.type=kemo` 可按配置使用图片、音频、视频与媒体生成能力。
- 主模型已直接收到某项媒体时，不要重复调用本工具。
- 只有附件说明要求调用本工具，或用户明确要求媒体生成/转换时才调用。
- 输入理解优先由明确支持对应模态的主模型完成；本工具负责专用模型回退和生成类能力。
- `paths` 支持绝对路径和相对项目根目录的路径，复用原生附件授权链：当前用户 download/file_upload、当前 Run 用户原文明确给出的路径，或此前成功工具结果报告的路径才可使用。失败结果、工具参数或本工具 instruction 不构成授权，子代理只使用自身输入及工具结果。
- 先拒绝跨用户文件、远程 URL、符号链接与目录联接（包括父目录），验证来源、普通文件类型、签名和大小后才创建 Provider、查询能力、编码或上传；路径授权不改变 asset_ids 的现有来源校验。不要猜路径或改写成 Markdown 图片。
- 图片在请求前会完整解码校验；Chat 图片通道只接受 JPEG、PNG、WEBP 和 GIF，其他图片格式应先明确提示用户转换。
- `analyze_image`、`transcribe_audio`、`analyze_video` 遇到可重试的瞬时上游错误时最多再尝试一次；生成与编辑类动作不自动重试，避免重复计费或产生重复产物。
- 专用调用默认使用当前工具期限并预留收尾时间；用户显式配置 `provider.timeout` 时尊重该值，但不会越过工具期限。

## Action

| action | 输入 | 输出 | 配置项 |
| --- | --- | --- | --- |
| `analyze_image` | 1-8 张图片 | 文本 | `vision` |
| `generate_image` | 文本指令 | 图片产物 | `image_generation` |
| `edit_image` | 图片；可附 mask/reference | 图片产物 | `image_edit` |
| `transcribe_audio` | 1-8 个音频 | 文本 | `audio_transcription` |
| `generate_speech` | 文本指令 | 音频产物 | `speech_generation` |
| `convert_speech` | 音频及转换指令 | 音频产物 | `speech_to_speech` |
| `analyze_video` | 1-8 个视频 | 文本 | `video_understanding` |
| `generate_video` | 文本，可附图片/视频参考 | 视频产物 | `video_generation` |

生成产物由框架下载、校验并保存到当前用户的 `download` 文件空间；工具只返回稳定的产物描述，不返回 Base64、临时 URL 或服务器绝对路径。
