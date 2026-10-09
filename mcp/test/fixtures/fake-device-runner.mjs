#!/usr/bin/env node
import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';

const TOOL_NAMES = [
  'test_list_devices','test_doctor','plugin_get_sdk_info','plugin_doctor',
  'plugin_list_installed','plugin_inspect_installed','plugin_get_status','plugin_install','plugin_update','plugin_reload','plugin_enable','plugin_disable','plugin_get_logs','plugin_capture_settings','plugin_open_chat','plugin_open_dialogs',
  'plugin_capture_screen','plugin_assert','plugin_get_diagnostics','test_reset_state','test_run','test_collect_evidence',
  'development_start','development_submit_revision','development_execute_iteration','development_get','knowledge_propose_from_test'
];

function buildRunner() {
  const server = new McpServer({ name: 'extera-plugin-test-mcp', version: '0.1.0' }, { capabilities: { tools: { listChanged: false } } });
  for (const name of TOOL_NAMES) {
    server.registerTool(name, {
      title: `Fixture ${name}`,
      description: 'Closed-loop proxy integration fixture.',
      inputSchema: z.object({}).passthrough(),
      outputSchema: z.object({ ok: z.boolean(), data: z.unknown().optional() }).passthrough()
    }, async input => {
      const data = name === 'test_list_devices'
        ? [{ serial: 'fixture-device', state: 'device', details: 'fixture' }]
        : { tool: name, echo: input };
      const structuredContent = { ok: true, data };
      return { content: [{ type: 'text', text: JSON.stringify(structuredContent) }], structuredContent };
    });
  }
  return server;
}

serveStdio(() => buildRunner(), {
  legacy: 'serve',
  onerror: error => console.error(`[fake-device-runner] ${error.stack || error.message}`)
});
