const { createReadStream } = require('node:fs');
const { createInterface } = require('node:readline');
const { parseArgs } = require('node:util');

const UNKNOWN = 'unknown';
const USAGE = 'Usage: aesop excerpt --log <file|-> [--job <id-or-name>] [--step <name>]';
// gh run view --log adds two tab-separated columns before the Actions timestamp.
const envelope = /^(?:([^\t]+)\t([^\t]+)\t)?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z /;
const pythonHeading = /^(?:FAIL|ERROR): (\w+) \((\w+(?:\.\w+)+)\)((?: [(\[].*)?)$/;
const shellHeading = /^(?:=== |\[TEST\] )((?:Test|TEST)(?: \w+)?[: ].*?)(?: ===)?$/;

function record(job, step) {
  return { job, step, suite: UNKNOWN, subcase: UNKNOWN, test_id: UNKNOWN, parent: UNKNOWN, assertion: UNKNOWN };
}

async function extract(input, labels) {
  const hits = [];
  let job, step, sectionStart, shell, python;
  let number = 0;
  for await (const text of createInterface({ input, crlfDelay: Infinity })) {
    const source = { line: ++number, text };
    const row = number === 1 ? text.replace(/^\uFEFF/, '') : text;
    const prefix = envelope.exec(row);
    const message = prefix ? row.slice(prefix[0].length) : row;
    const rowJob = prefix?.[1] ?? labels.job ?? UNKNOWN;
    const rowStep = prefix?.[2] ?? labels.step ?? UNKNOWN;
    if (rowJob !== job || rowStep !== step) {
      job = rowJob;
      step = rowStep;
      sectionStart = hits.length;
      shell = python = undefined;
    }

    if (message.startsWith('[TEST] Running: ')) {
      shell = { suite: message.slice('[TEST] Running: '.length) || UNKNOWN, start: hits.length };
      python = undefined;
      continue;
    }
    const suiteEnd = /^\[(PASS|FAIL)\] (.+?)(?: \(exit code: -?\d+\))?$/.exec(message);
    if (suiteEnd && shell && shell.suite === suiteEnd[2]) {
      if (suiteEnd[1] === 'FAIL' && shell.start === hits.length) {
        hits.push({ ...record(job, step), suite: suiteEnd[2] });
      }
      shell = python = undefined;
      continue;
    }
    const heading = shellHeading.exec(message);
    if (heading) {
      shell ??= { suite: UNKNOWN, start: hits.length };
      python = undefined;
      shell.subcase = heading[1];
      shell.parent = source;
      continue;
    }

    const unit = !shell && pythonHeading.exec(message);
    if (unit) {
      const [, method, location, detail] = unit;
      const qualified = location.endsWith('.' + method);
      const suite = qualified ? location.slice(0, -(method.length + 1)) : location;
      const id = qualified ? location : location + '.' + method;
      const hit = { ...record(job, step), suite, subcase: method + detail, test_id: id + detail, parent: source };
      hits.push(hit);
      python = { hit, assertions: 0, sawDivider: false };
    } else if (message.startsWith('FAIL: ')) {
      const hit = record(job, step);
      hit.assertion = source;
      if (shell) {
        hit.suite = shell.suite;
        if (shell.parent) {
          hit.subcase = shell.subcase;
          hit.parent = shell.parent;
          if (shell.suite !== UNKNOWN) hit.test_id = shell.suite + ' :: ' + shell.subcase;
        }
      }
      hits.push(hit);
      python = undefined;
    } else if (/^AssertionError(?::|$)/.test(message)) {
      if (python) {
        python.hit.assertion = ++python.assertions === 1 ? source : UNKNOWN;
      } else {
        hits.push({ ...record(job, step), assertion: source });
      }
    } else if (python && /^-{5,}$/.test(message) && !python.sawDivider && !python.assertions) {
      // unittest prints a divider between the heading and traceback, then another at the end.
      python.sawDivider = true;
    } else if (/^(?:[-=]{5,}|Ran \d+ tests?\b.*)$/.test(message)) {
      python = undefined;
    } else if (/^##\[error\]Process completed with exit code [1-9]\d*\./.test(message) && hits.length === sectionStart) {
      hits.push(record(job, step));
    }
  }
  return hits;
}

async function run(args) {
  try {
    const { values } = parseArgs({ args, options: {
      log: { type: 'string' }, job: { type: 'string' }, step: { type: 'string' },
      help: { type: 'boolean', short: 'h' },
    } });
    if (values.help) {
      console.log(USAGE + '\nPrint JSON failure excerpts from a raw step log or gh run view --log.');
      return;
    }
    if (!values.log) throw new Error(USAGE);
    const input = values.log === '-' ? process.stdin : createReadStream(values.log, { encoding: 'utf8' });
    console.log(JSON.stringify(await extract(input, values), null, 2));
  } catch (error) {
    console.error('aesop excerpt: ' + error.message);
    process.exitCode = 2;
  }
}

module.exports = { run };
