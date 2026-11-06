# SQLite simple dependency

Vendored from [wangfenjin/simple v0.7.1](https://github.com/wangfenjin/simple/releases/tag/v0.7.1).
Kemo uses the MIT option of upstream's dual license; see `LICENSE.simple` and `LICENSE.cppjieba`.
The cppjieba revision pinned by that release is `194c144d8b5ed1baf3190d07c5226e804454ab47`.

`deploy/vendor_simple.py --all` reproduces this directory from the upstream release assets.
It verifies the pinned SHA-256 of every ZIP, copies only the native library and the five
required dictionaries, and normalizes dictionary CRLF to LF. No upstream example scripts,
SQLite executables, build tools, hooks or external services are installed.
Commit the entire directory along with the integration before normal release packaging.
Runtime never downloads dependencies or falls back to a different tokenizer.

Platforms included: Windows x64/x86/ARM64, Linux x64/ARM64, macOS x64/ARM64.
Python's SQLite must provide FTS5 and `enable_load_extension`/`load_extension`.
Windows requires the matching Microsoft Visual C++ 2015–2022 runtime.
Windows dictionary paths are passed using its native code page (short aliases when
needed). If a path cannot be represented and the filesystem provides no usable short
name, Kemo reports an explicit error before invoking cppjieba, instead of risking a native abort.
Linux requires glibc and libstdc++6; Alpine/musl is not supported by these upstream binaries.
The x64 binary requires GLIBC >= 2.32 and GLIBCXX >= 3.4.29; ARM64 requires
GLIBC >= 2.38 and GLIBCXX >= 3.4.32. Older targets need a build against their own runtime,
not a silent switch to a different binary. macOS also needs a Python build permitting SQLite extensions.

Kemo uses `tokenize='simple 0'` with `jieba_query(query,0)` for Chinese/English keyword
search. Pinyin expansion is intentionally disabled to avoid treating English keywords as
Chinese pinyin. FTS tables are external-content indexes, not authoritative copies of data.
