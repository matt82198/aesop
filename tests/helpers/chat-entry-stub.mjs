// Stub for chat/cli.mjs, substituted by tests/helpers/chat-entry-stub-hooks.mjs in the bin/cli.js
// wiring tests. The stub ignores its inputs: shouldEnterChat returns whatever the test put in
// AESOP_TEST_STUB_SHOULD_ENTER ("1" = enter, anything else = do not enter), and runChat writes a
// marker line so the test can tell that bin/cli.js handed the terminal to chat.

// AESOP_TEST_STUB_THROW === '1' makes shouldEnterChat throw synchronously, so a test can observe how
// bin/cli.js reports a failure raised inside the post-import continuation.
export function shouldEnterChat() {
  if (process.env.AESOP_TEST_STUB_THROW === '1') {
    throw new Error('injected shouldEnterChat failure');
  }
  return process.env.AESOP_TEST_STUB_SHOULD_ENTER === '1';
}

export async function runChat({ stdout }) {
  stdout.write('STUB-CHAT-ENTERED\n');
  return 0;
}
