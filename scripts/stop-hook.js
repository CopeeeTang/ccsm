#!/usr/bin/env node
/**
 * CCSM Stop Hook — triggered after each Claude response.
 * Reads session data from stdin, triggers incremental index refresh.
 */
const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');

// H-3 fix: unified Python search order across all scripts
// L-3 fix: consistent order: venv > .venv > ml_env > system
const pythonPaths = [
  path.join(pluginRoot, 'venv', 'bin', 'python3'),
  path.join(pluginRoot, '.venv', 'bin', 'python3'),
  '/home/v-tangxin/GUI/ml_env/bin/python3',
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

    // H-3 fix: use execFileSync with array args to avoid shell injection
    execFileSync(pythonCmd, ['-c', 'from ccsm.core.index_db import incremental_refresh; incremental_refresh()'], {
      cwd: pluginRoot,
      env: { ...process.env, PYTHONPATH: pluginRoot },
      timeout: 10000,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
  } catch (err) {
    process.exit(0);
  }
});
