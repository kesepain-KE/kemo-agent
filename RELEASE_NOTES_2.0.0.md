# 2.0.0

This release replaces the plugin tool contract and is intentionally incompatible
with old tool names and parameter semantics.

- Every plugin declares an internal permission category: `read`, `write`,
  `delete`, or `external`. Permission metadata is enforced locally and is not
  sent to the Provider.
- User configuration can restrict available plugin permission categories with
  `plugins.permissions`. An omitted value keeps all categories enabled.
- `file.search` now accepts only `mode: "name"` or `mode: "content"`. The old
  values are rejected; `file`/`name` behavior is represented by `name`, and
  `text`/`content`/`code` behavior by `content`.
- `web_search.search` replaces `include_answer` and `include_raw_content` with
  `answer: none|basic|advanced` and `raw_content: none|markdown|text`. Old
  parameters are rejected.
- The `kemo_graph` guide tool is replaced by `kemo_graph_read` and
  `kemo_graph_write`. The read tool cannot generate write operations, and the
  write tool is removed when write permission is disabled.
- Subagent manifests may restrict plugin categories with
  `tools.plugins.permissions`. If a subagent declares an explicit plugin name
  allowlist, renamed or unknown tools cause startup validation to fail.
