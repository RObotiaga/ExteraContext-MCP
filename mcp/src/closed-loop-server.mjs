import { buildServer } from './server.mjs';
import { registerDeviceTools } from './device-tools.mjs';

export function buildClosedLoopServer(options = {}) {
  const server = buildServer(options);
  registerDeviceTools(server, options);
  return server;
}
