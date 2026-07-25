# multimodal

处理当前一轮已登记的媒体、显式本地媒体路径，或调用 Kemo 专用模型生成媒体。附件优先使用当前 Run 说明中的 `asset_id`；当用户或工具结果给出了明确路径时，也可通过 `paths` 直接提交本地媒体。

## 模式边界

- `provider.type=chat` 只保证 `analyze_image`；其他动作必须明确提示改用 Kemo 协议。
- `provider.type=kemo` 可按配置使用图片、音频、视频与媒体生成能力。
- 主模型已直接收到某项媒体时，不要重复调用本工具。
- 只有附件说明要求调用本工具，或用户明确要求媒体生成/转换时才调用。
- 输入理解优先由明确支持对应模态的主模型完成；本工具负责专用模型回退和生成类能力。
- `paths` 支持绝对路径和相对项目根目录的路径。路径会先验证文件存在、普通文件类型、媒体签名与大小，再直接编码（Chat 图片）或上传（Kemo Asset）给专用模型；不要先把路径改写成 Markdown 图片。
- 不得猜测路径。只有路径由用户明确提供、当前 Run 的工具结果返回，或已经通过文件工具确认时才能使用。

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

## Tool

```json
{
  "name": "multimodal",
  "description": "使用主 Run 明确授权的资产调用专用多模态模型，支持图片理解/生成/编辑、音频转写/生成/转换以及视频理解/生成。Chat 模式只支持图片理解，完整能力要求 Kemo 模式。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["analyze_image", "generate_image", "edit_image", "transcribe_audio", "generate_speech", "convert_speech", "analyze_video", "generate_video"]
      },
      "asset_ids": {
        "type": "array",
        "items": {"type": "string", "pattern": "^asset_[A-Za-z0-9_.:-]+$"},
        "maxItems": 8,
        "description": "当前 Run 附件说明中出现的本地稳定资产标识；纯生成动作可省略"
      },
      "paths": {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "maxItems": 8,
        "description": "明确的本地媒体路径；支持绝对路径或相对项目根目录路径，与 asset_ids 合计最多 8 项"
      },
      "instruction": {
        "type": "string",
        "minLength": 1,
        "maxLength": 8000,
        "description": "分析、生成、编辑、转写或转换指令"
      },
      "detail": {"type": "string", "enum": ["auto", "low", "high"]},
      "output_format": {"type": "string", "maxLength": 20},
      "voice": {"type": "string", "maxLength": 100},
      "size": {"type": "string", "maxLength": 40},
      "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
      "asset_roles": {
        "type": "array",
        "items": {"type": "string", "enum": ["source", "mask", "reference", "style", "first_frame", "last_frame"]},
        "maxItems": 8
      }
    },
    "required": ["action", "instruction"],
    "additionalProperties": false
  },
  "version": "2.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
