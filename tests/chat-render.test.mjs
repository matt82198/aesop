import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// S1 spec for chat/render.mjs (the pure frame renderer of the interactive chat TUI).
// Every test drives the real module; nothing greps source text. All tests fail until
// chat/ exists (import failure), which is the intended red for S1.
//
// Pinned surface (see the S1 hand-back for the list of assumptions):
//   renderFrame(state, {columns, rows, color, unicode}) -> string (lines joined by '\n')
//   resolveColor({env, argv, isTTY}) -> boolean   (NO_COLOR env and --no-color both force false)
//   resolveUnicode({env, unicode}) -> boolean     (explicit option wins; TERM=dumb forces false)
//
// Run: node --test --test-reporter=tap tests/chat-render.test.mjs

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const renderPath = join(repoRoot, 'chat', 'render.mjs');
const pkgVersion = JSON.parse(readFileSync(join(repoRoot, 'package.json'), 'utf8')).version;

const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
const stripAnsi = (s) => s.replace(ANSI, '');
const BOX_DRAWING = /[\u2500-\u257f]/;

async function loadRender() {
  return import(pathToFileURL(renderPath).href);
}

function makeState(over = {}) {
  return {
    transcript: [],
    inputBuffer: '',
    cursor: 0,
    status: {
      model: 'claude-haiku-5-5',
      provider: 'anthropic',
      cwd: 'C:/Users/Mattt/aesop',
      tokens: 0,
      elapsedMs: 0,
      spinnerTick: 0,
    },
    mode: 'idle',
    ...over,
  };
}

const OPTS = { columns: 80, rows: 24, color: false };

function lineContaining(frame, needle) {
  const hit = frame.split('\n').find((l) => stripAnsi(l).includes(needle));
  assert.ok(hit !== undefined, `no line contains ${JSON.stringify(needle)} in frame:\n${stripAnsi(frame)}`);
  return hit;
}

test('render_first_frame_shows_product_name_and_version', async () => {
  const { renderFrame } = await loadRender();
  const frame = renderFrame(makeState(), OPTS);
  const firstLine = stripAnsi(frame.split('\n').find((l) => stripAnsi(l).trim() !== ''));
  assert.match(firstLine, /aesop/i, `first frame line must carry the product name; got: ${firstLine}`);
  assert.ok(
    firstLine.includes(pkgVersion),
    `first frame line must carry the version ${pkgVersion}; got: ${firstLine}`,
  );
});

test('render_status_bar_shows_model_and_provider', async () => {
  const { renderFrame } = await loadRender();
  const frame = renderFrame(
    makeState({ status: { model: 'claude-opus-5', provider: 'anthropic', cwd: '/w', tokens: 12, elapsedMs: 1500, spinnerTick: 0 } }),
    OPTS,
  );
  const plain = stripAnsi(frame);
  assert.ok(plain.includes('claude-opus-5'), `status bar must show the model; frame:\n${plain}`);
  assert.ok(plain.includes('anthropic'), `status bar must show the provider; frame:\n${plain}`);
});

test('render_user_and_assistant_prompt_prefixes_differ', async () => {
  const { renderFrame } = await loadRender();
  const frame = renderFrame(
    makeState({
      transcript: [
        { role: 'user', text: 'USERMSG' },
        { role: 'assistant', text: 'ASSTMSG' },
      ],
    }),
    OPTS,
  );
  const userLine = stripAnsi(lineContaining(frame, 'USERMSG'));
  const asstLine = stripAnsi(lineContaining(frame, 'ASSTMSG'));
  const userPrefix = userLine.slice(0, userLine.indexOf('USERMSG'));
  const asstPrefix = asstLine.slice(0, asstLine.indexOf('ASSTMSG'));
  assert.ok(userPrefix.length > 0, 'user line must carry a prompt prefix before the message text');
  assert.notEqual(
    userPrefix,
    asstPrefix,
    `user and assistant lines must use visibly different prefixes; user=${JSON.stringify(userPrefix)} assistant=${JSON.stringify(asstPrefix)}`,
  );
});

test('render_streaming_assistant_spinner_glyph_changes_with_tick', async () => {
  const { renderFrame } = await loadRender();
  const base = {
    transcript: [{ role: 'assistant', text: 'partial answer', streaming: true }],
    mode: 'streaming',
  };
  const tick0 = stripAnsi(renderFrame(makeState({ ...base, status: { ...makeState().status, spinnerTick: 0 } }), OPTS));
  const tick1 = stripAnsi(renderFrame(makeState({ ...base, status: { ...makeState().status, spinnerTick: 1 } }), OPTS));
  assert.notEqual(tick0, tick1, 'a streaming message must render a spinner that changes with spinnerTick');

  const lines0 = tick0.split('\n');
  const lines1 = tick1.split('\n');
  assert.equal(lines1.length, lines0.length, 'changing the spinner tick must not change the line count');
  const differing = lines0.map((l, i) => [l, lines1[i]]).filter(([a, b]) => a !== b);
  assert.ok(differing.length >= 1, 'at least one line must differ between ticks');
  for (const [a, b] of differing) {
    assert.ok(
      a.includes('partial answer') && b.includes('partial answer'),
      `only the streaming message line may change between ticks; got ${JSON.stringify(a)} vs ${JSON.stringify(b)}`,
    );
  }
});

test('render_wrapping_never_exceeds_columns', async () => {
  const { renderFrame } = await loadRender();
  const longText = ('word '.repeat(120)).trim();
  const longCwd = 'C:/Users/Mattt/' + 'deeply/nested/'.repeat(20) + 'project';
  const state = makeState({
    transcript: [
      { role: 'user', text: longText },
      { role: 'assistant', text: longText },
      { role: 'tool', text: longText },
      { role: 'system', text: longText },
    ],
    inputBuffer: longText,
    cursor: longText.length,
    status: { ...makeState().status, cwd: longCwd },
  });
  for (const columns of [20, 40, 80]) {
    const frame = renderFrame(state, { columns, rows: 60, color: false });
    const lines = frame.split('\n');
    assert.ok(lines.length > 1, `a wrapped frame at ${columns} columns must span several lines`);
    for (const line of lines) {
      const visible = stripAnsi(line).length;
      assert.ok(
        visible <= columns,
        `line exceeds ${columns} columns (${visible} chars): ${JSON.stringify(stripAnsi(line))}`,
      );
    }
  }
});

test('render_color_false_emits_no_escape_byte', async () => {
  const { renderFrame } = await loadRender();
  const rich = makeState({
    transcript: [
      { role: 'user', text: 'u' },
      { role: 'assistant', text: 'a', streaming: true },
      { role: 'tool', text: 't' },
      { role: 'system', text: 's' },
    ],
    mode: 'streaming',
  });
  const frame = renderFrame(rich, { ...OPTS, color: false });
  assert.equal(frame.includes('\x1b'), false, 'color=false output must contain no 0x1b byte');
});

test('render_color_true_emits_escape_sequences', async () => {
  // Positive control for the test above: color=true must actually produce escapes,
  // otherwise a renderer that ignores the option would pass the no-escape test vacuously.
  const { renderFrame } = await loadRender();
  const frame = renderFrame(
    makeState({ transcript: [{ role: 'user', text: 'u' }, { role: 'assistant', text: 'a' }] }),
    { ...OPTS, color: true },
  );
  assert.ok(frame.includes('\x1b['), 'color=true output must contain ANSI SGR escapes');
});

test('render_no_color_env_and_no_color_flag_both_disable_color', async () => {
  const { resolveColor } = await loadRender();
  assert.equal(resolveColor({ env: { NO_COLOR: '1' }, argv: [], isTTY: true }), false, 'NO_COLOR env must yield color=false');
  assert.equal(resolveColor({ env: {}, argv: ['--no-color'], isTTY: true }), false, '--no-color must yield color=false');
  assert.equal(
    resolveColor({ env: {}, argv: [], isTTY: true }),
    true,
    'control: with no NO_COLOR and no --no-color on a TTY, color must be on',
  );
});

test('render_tool_message_is_visually_distinct_from_assistant_text', async () => {
  const { renderFrame } = await loadRender();
  const frame = renderFrame(
    makeState({
      transcript: [
        { role: 'tool', text: 'TOOLTEXT ran grep' },
        { role: 'assistant', text: 'ASSTTEXT answer' },
      ],
    }),
    OPTS,
  );
  const toolLine = stripAnsi(lineContaining(frame, 'TOOLTEXT'));
  const asstLine = stripAnsi(lineContaining(frame, 'ASSTTEXT'));
  const indent = (l) => l.length - l.trimStart().length;
  const toolIsIndented = indent(toolLine) > indent(asstLine);
  const toolHasBorder = /^\s*[|\u2502\u2503]/.test(toolLine);
  assert.ok(
    toolIsIndented || toolHasBorder,
    `a tool message must render as a distinct block (deeper indent or a box border); tool=${JSON.stringify(toolLine)} assistant=${JSON.stringify(asstLine)}`,
  );
});

test('render_ascii_fallback_when_unicode_is_not_safe', async () => {
  const { renderFrame, resolveUnicode } = await loadRender();
  const state = makeState({ transcript: [{ role: 'tool', text: 'TOOLTEXT' }] });
  const ascii = renderFrame(state, { ...OPTS, unicode: false });
  assert.equal(BOX_DRAWING.test(stripAnsi(ascii)), false, 'unicode=false must not emit box-drawing characters');
  assert.equal(
    resolveUnicode({ env: { TERM: 'dumb' } }),
    false,
    'TERM=dumb must not be treated as unicode-safe',
  );
  assert.equal(
    resolveUnicode({ env: { TERM: 'xterm-256color' }, unicode: false }),
    false,
    'an explicit unicode:false option must win over a capable TERM',
  );
});

test('render_box_drawing_used_when_unicode_is_safe', async () => {
  // Positive control: the fallback test would also pass if box drawing were never used.
  const { renderFrame, resolveUnicode } = await loadRender();
  const state = makeState({ transcript: [{ role: 'tool', text: 'TOOLTEXT' }] });
  const unicode = renderFrame(state, { ...OPTS, unicode: true });
  assert.equal(BOX_DRAWING.test(stripAnsi(unicode)), true, 'unicode=true must use box-drawing characters for the tool block');
  assert.equal(
    resolveUnicode({ env: { TERM: 'xterm-256color' }, unicode: true }),
    true,
    'an explicit unicode:true option must enable box drawing',
  );
});

test('render_force_color_env_enables_color_without_a_tty', async () => {
  const { resolveColor } = await loadRender();
  assert.equal(
    resolveColor({ env: { FORCE_COLOR: '1' }, isTTY: false }),
    true,
    'FORCE_COLOR=1 must enable color even when the stream is not a TTY',
  );
  assert.equal(
    resolveColor({ env: { FORCE_COLOR: 'true' }, isTTY: false }),
    true,
    'any non-empty FORCE_COLOR value must enable color',
  );
  assert.equal(
    resolveColor({ env: { FORCE_COLOR: '1', NO_COLOR: '1' }, isTTY: false }),
    false,
    'NO_COLOR must still win over FORCE_COLOR when both are set',
  );
  assert.equal(
    resolveColor({ env: { FORCE_COLOR: '1', NO_COLOR: '1' }, isTTY: true }),
    false,
    'NO_COLOR must still win over FORCE_COLOR on a TTY',
  );
});

test('render_resolve_unicode_reads_lc_all_then_lang_locale', async () => {
  const { resolveUnicode } = await loadRender();
  const xterm = { TERM: 'xterm-256color' };
  assert.equal(
    resolveUnicode({ env: { ...xterm, LC_ALL: 'C' } }),
    false,
    'LC_ALL=C is not a UTF locale; box drawing must fall back to ASCII',
  );
  assert.equal(
    resolveUnicode({ env: { ...xterm, LC_ALL: 'en_US.ISO-8859-1' } }),
    false,
    'LC_ALL naming a non-UTF charset must fall back to ASCII',
  );
  assert.equal(
    resolveUnicode({ env: { ...xterm, LANG: 'en_US.ISO-8859-1' } }),
    false,
    'LANG must be consulted when LC_ALL is absent',
  );
  assert.equal(
    resolveUnicode({ env: { ...xterm, LC_ALL: 'C', LANG: 'en_US.UTF-8' } }),
    false,
    'LC_ALL must win over LANG',
  );
  assert.equal(
    resolveUnicode({ env: { ...xterm, LC_ALL: 'en_US.UTF-8' } }),
    true,
    'a UTF-8 locale must allow box drawing',
  );
  assert.equal(
    resolveUnicode({ env: { TERM: 'dumb', LC_ALL: 'en_US.UTF-8' } }),
    false,
    'TERM=dumb must still force ASCII under a UTF-8 locale',
  );
});

test('render_every_line_fits_columns_at_narrow_widths', async () => {
  const { renderFrame } = await loadRender();
  const state = makeState({
    transcript: [
      { role: 'user', text: 'hello there' },
      { role: 'assistant', text: 'general kenobi' },
      { role: 'tool', text: 'tool output here' },
      { role: 'system', text: 'a system note' },
    ],
    inputBuffer: 'typed text',
    cursor: 5,
    hint: 'press ctrl-c again to quit',
  });
  for (const columns of [1, 5, 7, 12]) {
    let frame = '';
    assert.doesNotThrow(
      () => {
        frame = renderFrame(state, { columns, rows: 60, color: false, unicode: true });
      },
      `renderFrame must not throw at columns=${columns}`,
    );
    for (const line of stripAnsi(frame).split('\n')) {
      assert.ok(
        line.length <= columns,
        `line exceeds ${columns} columns (${line.length} chars): ${JSON.stringify(line)}`,
      );
    }
  }
});

test('render_frame_clips_to_rows_when_chrome_exceeds_rows', async () => {
  const { renderFrame } = await loadRender();
  const transcript = [];
  for (let i = 1; i <= 6; i += 1) {
    transcript.push({ role: 'user', text: `question ${i}` }, { role: 'assistant', text: `answer ${i}` });
  }
  const frame = renderFrame(makeState({ transcript, hint: 'a hint' }), { columns: 80, rows: 3, color: false });
  const lines = frame.split('\n');
  assert.ok(
    lines.length <= 3,
    `a frame rendered with rows=3 must have at most 3 lines (clip, never scroll); got ${lines.length}:\n${stripAnsi(frame)}`,
  );
});
