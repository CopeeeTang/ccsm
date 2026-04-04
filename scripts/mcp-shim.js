#!/usr/bin/env node
/**
 * CCSM MCP Shim — bridges Claude Code plugin system (expects Node.js)
 * to the Python FastMCP server via stdio pipe.
 *
 * Claude Code sends JSON-RPC over stdin → this shim → Python subprocess stdin
 * Python subprocess stdout → this shim → Claude Code reads from stdout
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// Resolve plugin root (CLAUDE_PLUGIN_ROOT or fallback)
const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');

// Find Python — prefer venv if available
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

// Spawn Python MCP server
const child = spawn(pythonCmd, ['-m', 'ccsm.mcp.server'], {
  cwd: pluginRoot,
  stdio: ['pipe', 'pipe', 'inherit'],  // inherit stderr for debugging
  env: { ...process.env, PYTHONPATH: pluginRoot },
});

// Bidirectional pipe: Claude Code stdin <-> Python stdin
process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);

// Handle process lifecycle
child.on('exit', (code) => process.exit(code || 0));
child.on('error', (err) => {
  process.stderr.write(`CCSM shim error: ${err.message}\n`);
  process.exit(1);
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
