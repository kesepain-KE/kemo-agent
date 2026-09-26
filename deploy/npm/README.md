# kemo-agent npm launcher

This package wraps the Python application; Python 3.10+ must already be installed.
No postinstall script is required. `kemo` installs the exact bundled framework
release into `~/.kemo-agent`, prepares an isolated Python environment, then starts
the existing setup/Web entrypoints. First start asks for user configuration.

Use `kemo check`, `kemo update --yes`, `kemo recover`, or
`kemo start -- --host=127.0.0.1 --port=1357`. Upgrade the npm package to obtain a
new bundled application release. `KEMO_INSTALL_ROOT` selects an empty destination;
it cannot take over an installation owned by the Windows/Linux installer.

Maintainers: **do not publish this source directory directly**. Run
`python deploy/pack.py`, review the generated archive, then publish its
`deploy/artifacts/<framework-version>/npm` staging directory. Version is generated
from the framework's root version.json. Registry namespace access is required.

