#!/usr/bin/env node
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');

const pythonPaths = [
  '/home/v-tangxin/GUI/ml_env/bin/python3',
  path.join(pluginRoot, '.venv', 'bin', 'python3'),
  'python3',
];

let pythonCmd = 'python3';
for (const p of pythonPaths) {
  if (p === 'python3' || fs.existsSync(p)) {
    pythonCmd = p;
    break;
  }
}

let stdinData = '';
process.stdin.on('data', (chunk) => { stdinData += chunk; });
process.stdin.on('end', () => {
  try {
    const sessionInfo = JSON.parse(stdinData);
    if (!sessionInfo.sessionId) process.exit(0);

    execSync(
      `${pythonCmd} -c "from ccsm.core.index_db import incremental_refresh; incremental_refresh()"`,
      {
        cwd: pluginRoot,
        env: { ...process.env, PYTHONPATH: pluginRoot },
        timeout: 10000,
        stdio: ['ignore', 'inherit', 'inherit'],
      }
    );
  } catch (err) {
    process.exit(0);
  }
});
