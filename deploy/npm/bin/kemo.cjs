#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
const base = path.resolve(__dirname, '..');
const entry = path.join(base, 'runtime', 'deploy.py');
const archive = path.join(base, 'release.zip');
if (!fs.existsSync(entry) || !fs.existsSync(archive)) {
  console.error('Publish/install the staging package produced by deploy/pack.py, not deploy/npm directly.');
  process.exit(1);
}
const pkg = require(path.join(base, 'package.json'));
const candidates = process.platform === 'win32' ? [['python'], ['py', '-3']] : [['python3'], ['python']];
const python = candidates.find(cmd => spawnSync(cmd[0], [...cmd.slice(1), '-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'], { stdio: 'ignore' }).status === 0);
if (!python) { console.error('Install Python 3.10+ first.'); process.exit(1); }
const root = process.env.KEMO_INSTALL_ROOT || path.join(os.homedir(), '.kemo-agent');
const argv = process.argv.slice(2);
const command = argv[0] || 'start';
const supported = new Set(['start', 'install', 'update', 'check', 'recover', 'init']);
if (!supported.has(command)) {
  console.error('Usage: kemo [start|install|update|check|recover|init] [deploy options] [-- Web options]');
  process.exit(2);
}
const shared = ['--platform', 'npm', '--install-root', root];
function execDeploy(action, options) {
  const child = spawnSync(python[0], [...python.slice(1), entry, action, ...shared, ...options], { stdio: 'inherit' });
  if (child.error) { console.error(child.error.message); process.exit(1); }
  if (child.status !== 0) process.exit(child.status || 1);
}
if (command === 'start') {
  // Provision the exact framework packaged with this npm version, never @latest.
  execDeploy('install', ['--source', archive, '--version', pkg.version, '--yes']);
  const child = spawn(python[0], [...python.slice(1), entry, 'start', ...shared, ...argv.slice(1)], { stdio: 'inherit' });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {
    if (process.platform === 'win32') {
      // Kill only this launcher's process tree, never unrelated Python services.
      if (child.pid) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill(signal);
    }
  });
  child.on('error', error => { console.error(error.message); process.exitCode = 1; });
  child.on('exit', code => { process.exitCode = code === null ? 1 : code; });
} else {
  const options = ['install', 'update', 'check'].includes(command) ? ['--source', archive, '--version', pkg.version] : [];
  execDeploy(command, [...options, ...argv.slice(1)]);
}
