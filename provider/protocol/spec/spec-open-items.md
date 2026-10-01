# Kemo 2.0 S0 未裁定项

截至 2026-09-30，wire 形状没有阻塞性未裁定项。以下属于生产接线验证项，而非协议歧义：

1. 各真实 Provider 对 `strict json_schema`、`allowed tool_choice`、`store=false` 的强保证必须逐厂商实测；未验证时能力保持关闭。
2. 多候选存储迁移须由网关执行层完成原子候选分配和合计 Usage 计费；在迁移完成前 profile 必须声明 `supports_multiple_choices=false`。
3. URL 媒体的 DNS 重绑定、跳转与字节上限由资产准备层验证，L1/L2 不联网。
4. 真实上游联调需要用户提供可用凭据并授权费用；离线假上游测试不能冒充真实联调。
