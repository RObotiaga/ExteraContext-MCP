import { readFileSync } from 'node:fs';

// package.json is the authoritative MCP release version. Root VERSION retains
// the bundle's historical `-mcp` suffix; see the repository docs for the mapping.
const packageJson = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

export const VERSION = packageJson.version;
