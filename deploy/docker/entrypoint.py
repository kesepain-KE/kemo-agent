import os
import subprocess
import sys

entry = '/opt/kemo-deploy/deploy.py'
common = ['--platform', 'docker', '--install-root', '/data', '--yes']
subprocess.run([sys.executable, entry, 'install', *common,
                '--source', '/opt/kemo-release.zip', '--runtime-python', sys.executable], check=True)
# Existing volumes are updated transactionally on every image version change.
os.execv(sys.executable, [sys.executable, entry, 'start', *common,
                        '--', '--host=0.0.0.0', '--port=1357', *sys.argv[1:]])

