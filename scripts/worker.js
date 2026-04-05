#!/usr/bin/env node
/**
 * CCSM Worker — triggers Python-side incremental indexing.
 * Called by hooks on SessionStart/SessionEnd to refresh the session index.
 */
const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');
const action = process.argv[2] || 'index-refresh';

// L-3 fix: unified Python search order (same as stop-hook.js and mcp-shim.js)
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

try {
  // H-3 fix: use execFileSync with array args to avoid shell injection
  if (action === 'index-refresh') {
    execFileSync(pythonCmd, ['-c', 'from ccsm.core.index_db import incremental_refresh; count = incremental_refresh(); print(f"CCSM: refreshed {count} sessions")'], {
      cwd: pluginRoot,
      env: { ...process.env, PYTHONPATH: pluginRoot },
      timeout: 25000,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
  } else if (action === 'session-ended') {
    execFileSync(pythonCmd, ['-c', 'from ccsm.core.index_db import incremental_refresh; incremental_refresh()'], {
      cwd: pluginRoot,
      env: { ...process.env, PYTHONPATH: pluginRoot },
      timeout: 8000,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
  }
} catch (err) {
  // Non-fatal: index refresh is best-effort
  process.stderr.write(`CCSM worker (${action}): ${err.message}\n`);
}
