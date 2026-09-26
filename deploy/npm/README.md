# kemo-agent npm launcher

安装（免 registry 凭据，直接从 GitHub Release 资产下载）：

```sh
npm install -g https://github.com/kesepain-KE/kemo-agent/releases/latest/download/kemo-agent-npm.tgz
kemo
```

升级：先停止应用，然后重跑同一条命令 —— URL 里的 `latest` 永远指向最新发布版：

```sh
npm install -g https://github.com/kesepain-KE/kemo-agent/releases/latest/download/kemo-agent-npm.tgz
kemo
```

Pushing repository code does not publish this package. The npm version is generated
from the framework's `version.json.version`; there is no separate launcher version.
`kemo check` and `kemo update` use the locally installed package's bundled release,
not the latest GitHub Release. Do not mix the legacy `update.py` updater with this
installation. Node.js 18+ and a working Python virtual environment are required.

This package wraps the Python application; Python 3.10+ must already be installed.
No postinstall script is required. `kemo` installs the exact bundled framework
release into `~/.kemo-agent`, prepares an isolated Python environment, then starts
the existing setup/Web entrypoints. First start asks for user configuration.

Use `kemo check`, `kemo update --yes`, `kemo recover`, or
`kemo start -- --host=127.0.0.1 --port=1357`. Upgrade the npm package to obtain a
new bundled application release. `KEMO_INSTALL_ROOT` selects an empty destination;
it cannot take over an installation owned by the Windows/Linux installer.

Maintainers: **do not publish this source directory directly**. Run
`python deploy/pack.py`, review the generated archive, then upload the packed
tarball to the matching GitHub Release — both under a versioned name and under the
fixed name `kemo-agent-npm.tgz`, so the `releases/latest/download/` URL keeps
pointing at the newest release. Version comes from the framework's root version.json.

