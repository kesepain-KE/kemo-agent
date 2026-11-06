# Framework tokenizer

All providers and models use the same pinned DeepSeek V4 tokenizer for local
text counting. Provider-reported usage remains authoritative for actual requests;
local counts are used for capacity checks and missing or estimated usage fields.

- Source: `https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip`
- Archive SHA-256:
  `e7310d1dafe0a86d8a5629fe78a7c763760f651db9b8682718a1781dcd6fe495`
- `deepseek_v4_tokenizer/tokenizer.json` SHA-256:
  `89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614`

`provider/tokenizer.py` verifies the archive and loads only `tokenizer.json` using
the `tokenizers` runtime. It does not execute the Python code included in the
archive, fetch assets at runtime, or load a Transformers/PyTorch model.

The asset has no truncation or padding configured. `add_special_tokens=False`
counts text without automatically adding BOS/EOS. Request framing and native
media reserves are framework estimates, not a provider billing guarantee.

The release collector includes this directory. New source assets must be included
in the release checkout; `deploy/pack.py --include-untracked` is available for
local development packaging.
