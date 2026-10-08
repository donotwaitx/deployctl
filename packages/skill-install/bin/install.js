#!/usr/bin/env node

/**
 * Universal installer for deployctl Agent Skill.
 * Supports Claude Code, Cursor, Antigravity, and other AI coding agents.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const args = process.argv.slice(2);

function printHelp() {
  console.log(`
🚀 deployctl Agent Skill Installer

Usage:
  npx deployctl-skill [options]

Options:
  -a, --agent <name>     Target agent: 'claude', 'cursor', 'antigravity', or 'all' (default: 'all')
  -g, --global           Install globally in user home profile (default)
  -p, --project          Install locally in current working directory
  -h, --help             Show this help message

Examples:
  npx deployctl-skill                      # Install globally for all agents
  npx deployctl-skill --agent claude       # Install for Claude Code
  npx deployctl-skill --project            # Install locally in active project
`);
}

if (args.includes('-h') || args.includes('--help')) {
  printHelp();
  process.exit(0);
}

let agent = 'all';
let isGlobal = true;

for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '-a' || arg === '--agent') {
    agent = (args[i + 1] || 'all').toLowerCase();
    i++;
  } else if (arg === '-p' || arg === '--project') {
    isGlobal = false;
  } else if (arg === '-g' || arg === '--global') {
    isGlobal = true;
  }
}

// Locate skill source
const skillSourcePath = path.resolve(__dirname, '../skills/deployctl/SKILL.md');
if (!fs.existsSync(skillSourcePath)) {
  console.error(`✖ Error: SKILL.md not found at ${skillSourcePath}`);
  process.exit(1);
}

const skillContent = fs.readFileSync(skillSourcePath, 'utf8');
const installedFiles = [];

function ensureWrite(targetPath, content) {
  const dir = path.dirname(targetPath);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(targetPath, content, 'utf8');
  installedFiles.push(targetPath);
}

const homeDir = os.homedir();
const cwd = process.cwd();

// 1. Claude Code
if (agent === 'claude' || agent === 'all') {
  const claudeTarget = isGlobal
    ? path.join(homeDir, '.claude', 'skills', 'deployctl', 'SKILL.md')
    : path.join(cwd, '.claude', 'skills', 'deployctl', 'SKILL.md');
  ensureWrite(claudeTarget, skillContent);
}

// 2. Cursor
if (agent === 'cursor' || agent === 'all') {
  const cursorTarget = isGlobal
    ? path.join(homeDir, '.cursor', 'rules', 'deployctl.md')
    : path.join(cwd, '.cursor', 'rules', 'deployctl.md');
  ensureWrite(cursorTarget, skillContent);
}

// 3. Antigravity MCP Config
if (agent === 'antigravity' || agent === 'all') {
  const antigravityConfigs = [
    path.join(homeDir, '.gemini', 'antigravity', 'mcp_config.json'),
    path.join(homeDir, '.gemini', 'config', 'mcp_config.json'),
  ];

  for (const cfgFile of antigravityConfigs) {
    if (fs.existsSync(path.dirname(cfgFile))) {
      let data = {};
      if (fs.existsSync(cfgFile)) {
        try {
          data = JSON.parse(fs.readFileSync(cfgFile, 'utf8'));
        } catch {
          data = {};
        }
      }
      if (!data.mcpServers) data.mcpServers = {};
      data.mcpServers.deployctl = {
        command: 'deployctl',
        args: ['mcp'],
      };
      fs.writeFileSync(cfgFile, JSON.stringify(data, null, 2), 'utf8');
      installedFiles.push(cfgFile);
    }
  }
}

console.log('\n✔ deployctl Agent Skill installed successfully!');
for (const f of installedFiles) {
  console.log(`  • ${f}`);
}
console.log('\nAI coding agents are now equipped with deployctl safe deployment workflows.\n');
