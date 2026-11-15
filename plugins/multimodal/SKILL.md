# multimodal

如果需要当前主模型自己结合上下文接收已取得的文件或媒体，使用 `attach_media` 装载到当前 Run
的下一次请求；本工具仍用于专用模型分析、转写与生成，不替代原生主模型媒体输入。

处理当前一轮已登记的文件或媒体、显式本地路径，或调用 Kemo 专用模型生成媒体。附件优先使用当前 Run 说明中的 `asset_id`，也可引用当前网关已授权的 Asset；当用户或工具结果给出了明确路径时，可通过 `paths` 提交本地文件或媒体。

## 模式边界

- `provider.type=chat` 只保证 `analyze_image`；其他动作必须明确提示改用 Kemo 协议。
- `provider.type=kemo` 可按配置使用文件理解、图片、音频、视频与媒体生成能力。
- 主模型已直接收到某个文件或媒体时，不要重复调用本工具。
- 仅在资产说明要求专用处理，或用户明确要求文件/媒体分析、生成或转换时调用。
- 输入理解优先由明确支持对应模态的主模型完成；本工具负责专用模型回退和生成类能力。
- `paths` 支持绝对路径和相对项目根目录的路径，复用原生附件授权链：当前用户 download/file_upload、当前 Run 用户原文明确给出的路径，或此前成功工具结果报告的路径才可使用。失败结果、工具参数或本工具 instruction 不构成授权，子代理只使用自身输入及工具结果。
- 先拒绝跨用户文件、远程 URL、符号链接与目录联接（包括父目录），验证来源、普通文件类型、签名和大小后才创建 Provider、查询能力、编码或上传；路径授权不改变 asset_ids 的现有来源校验。不要猜路径或改写成 Markdown 图片。
- 图片在请求前会完整解码校验；Chat 图片通道只接受 JPEG、PNG、WEBP 和 GIF，其他图片格式应先明确提示用户转换。
- 与 `attach_media` 对应接收图片、音频、视频和文件，使用同一套本地路径授权与单文件上限；
  网关已有资产共用类型、大小、有效期及内容块构造。Kemo 同样保留图片 `detail`，
  图片理解/编辑检查模型的 `max_images`，本地上传就绪后核对网关返回的校验和。
- 原生装载的 `conversation` 与本工具的专用操作分别检查，不能用主模型的能力声明替代专用
  模型声明。本工具返回专用模型的结果，不把原文件额外装载到主模型，也不继承整个主对话。
- `analyze_image`、`transcribe_audio`、`analyze_video`、`analyze_file` 遇到可重试的瞬时上游错误时最多再尝试一次；生成与编辑类动作不自动重试，避免重复计费或产生重复产物。
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
| `analyze_file` | 1-8 个普通文件 | 文本分析 | `file_understanding` |

## 文件理解

- 需要文档理解、摘要、信息提取或基于文件问答时使用 `analyze_file`；只需读取文本或修改文件时使用 `file` 工具。
- 必须配置 `multimodal_models.file_understanding`。专用模型须声明 `file` 输入和 `text` 输出；具体可接受的格式由该模型及网关适配器决定，不保证所有文件格式均可解析。
- Kemo 文件输入沿用 `conversation` 操作和 `FileContent`，不会要求网关声明不存在的 `file_understanding` 操作；明确禁用 `conversation` 的模型仍不能使用。
- 使用 `asset_ids` 或已授权 `paths`，合计 1-8 个文件；本地普通文件单项最大 100 MiB，Web 上传仍受其 80 MiB 限制。复用已有来源、跨用户、链接、校验和及大小校验，不自动提取全文塞进上下文。
- 已直接交给主模型的文件不重复分析。主模型未接收文件且已配置专用模型时，附件说明会提示调用本动作；未配置时仍使用 `file` 工具，不静默换模型。
- 返回模型生成的 `text` 和 `analysis`，不自动把原文件全文或 Base64 放进工具结果；输入文件本身不被修改。

生成产物由框架下载、校验并保存到当前用户的 `download` 文件空间；工具只返回稳定的产物描述，不返回 Base64、临时 URL 或服务器绝对路径。
用户上传、消息附件、运行中追加附件、已装载路径、主模型与本工具保存的产物，
以及 `expand_call` 成功发布的拓展产物，
其已登记的 `asset_id` 都可供本工具或 `attach_media` 在同一来源和会话继续使用。
本地接续引用默认存活 120 秒，
由全局 `media_assets.ttl_seconds` 按秒配置，产物的 `reference_expires_at` 给出到期时间；
读取不续期，仅同一用户、来源和会话可复用。网关原 ID 失效时会使用已验证的本地文件，
过期只清理引用，文件仍可通过获准的 `paths` 使用。
