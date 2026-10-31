// Loaded with `node --import <this file URL>` by the bin/cli.js chat-entry wiring tests. Registers
// tests/helpers/chat-entry-stub-hooks.mjs so chat/cli.mjs resolves to the stub for that child only.

import { register } from 'node:module';

register('./chat-entry-stub-hooks.mjs', import.meta.url);
