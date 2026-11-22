const { createReadStream } = require('node:fs');
const { readFile } = require('node:fs/promises');
const { createInterface } = require('node:readline');
const { parseArgs } = require('node:util');

const UNKNOWN = 'unknown';
const USAGE = 'Usage: aesop excerpt --log <file|-> [--job <id-or-name>] [--step <name>] [--jobs <jobs.json>]';
// gh run view --log adds two tab-separated columns before the Actions timestamp.
const columns = /^([^\t]+)\t([^\t]+)\t/;
const timestamp = /^\uFEFF?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z(?: |$)/;
const pythonHeading = /^(?:FAIL|ERROR): (\w+) \((\w+(?:\.\w+)+)\)((?: [(\[].*)?)$/;
const shellHeading = /^(?:=== |\[TEST\] )((?:Test|TEST)(?: \w+)?[: ].*?)(?: ===)?$/;

function stepAnchor(jobs, name, step) {
  const matches = jobs.filter(job => job.name === name || job.databaseId?.toString() === name);
  if (matches.length !== 1 || step === 'UNKNOWN STEP') return;
  const [job] = matches;
  const steps = job.steps.filter(s => s.name === step);
  if (steps.length !== 1 || !Number.isSafeInteger(steps[0].number) || steps[0].number < 1) return;
  if (!/^https?:\/\/[^/?#]+\/[^/?#]+\/[^/?#]+\/actions\/runs\/\d+\/job\/\d+$/.test(job.url)) return;
  return job.url + '#step:' + steps[0].number + ':';
}

function record(job, step, source) {
  return { job, step, source, suite: UNKNOWN, subcase: UNKNOWN, test_id: UNKNOWN, parent: UNKNOWN, assertion: UNKNOWN };
}

async function extract(input, labels, jobs) {
  const hits = [];
  let job, step, sectionStart, shell, python, anchor, stepLine;
  let number = 0;
  let ghLog = false;
  for await (const text of createInterface({ input, crlfDelay: Infinity })) {
    number++;
    const row = number === 1 ? text.replace(/^\uFEFF/, '') : text;
    const fields = columns.exec(row);
    const body = fields ? row.slice(fields[0].length) : row;
    if (!ghLog && fields && !timestamp.test(row)) {
      ghLog = timestamp.test(body) || body === '' || jobs.some(j => j.name === fields[1]);
    }
    const prefix = ghLog && fields;
    const message = (prefix ? body : row).replace(timestamp, '');
    const rowJob = prefix?.[1] ?? labels.job ?? UNKNOWN;
    const rowStep = prefix?.[2] ?? labels.step ?? UNKNOWN;
    if (rowJob !== job || rowStep !== step) {
      job = rowJob;
      step = rowStep;
      stepLine = 0;
      anchor = stepAnchor(jobs, job, step);
      sectionStart = hits.length;
      shell = python = undefined;
    }
    const source = { line: ++stepLine, text };
    const pointer = anchor ? anchor + stepLine : labels.log + ':' + number;

    if (message.startsWith('[TEST] Running: ')) {
      shell = { suite: message.slice('[TEST] Running: '.length) || UNKNOWN, start: hits.length };
      python = undefined;
      continue;
    }
    const suiteEnd = /^\[(PASS|FAIL)\] (.+?)(?: \(exit code: -?\d+\))?$/.exec(message);
    if (suiteEnd && shell && shell.suite === suiteEnd[2]) {
      if (suiteEnd[1] === 'FAIL' && shell.start === hits.length) {
        hits.push({ ...record(job, step, pointer), suite: suiteEnd[2] });
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
      const hit = { ...record(job, step, pointer), suite, subcase: method + detail, test_id: id + detail, parent: source };
      hits.push(hit);
      python = { hit, headingPointer: pointer, assertions: 0, sawDivider: false };
    } else if (message.startsWith('FAIL: ')) {
      const hit = record(job, step, pointer);
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
        python.hit.source = python.assertions === 1 ? pointer : python.headingPointer;
      } else {
        hits.push({ ...record(job, step, pointer), assertion: source });
      }
    } else if (python && /^-{5,}$/.test(message) && !python.sawDivider && !python.assertions) {
      // unittest prints a divider between the heading and traceback, then another at the end.
      python.sawDivider = true;
    } else if (/^(?:[-=]{5,}|Ran \d+ tests?\b.*)$/.test(message)) {
      python = undefined;
    } else if (/^##\[error\]Process completed with exit code [1-9]\d*\./.test(message) && hits.length === sectionStart) {
      hits.push(record(job, step, pointer));
    }
  }
  return hits;
}

async function run(args) {
  try {
    const { values } = parseArgs({ args, options: {
      log: { type: 'string' }, job: { type: 'string' }, step: { type: 'string' },
      jobs: { type: 'string' },
      help: { type: 'boolean', short: 'h' },
    } });
    if (values.help) {
      console.log(USAGE + '\nPrint JSON failure excerpts from a raw step log or gh run view --log.');
      return;
    }
    if (!values.log) throw new Error(USAGE);
    const jobs = values.jobs ? JSON.parse((await readFile(values.jobs, 'utf8')).replace(/^\uFEFF/, '')).jobs : [];
    if (!Array.isArray(jobs)) throw new Error('--jobs expects gh run view --json jobs output');
    const input = values.log === '-' ? process.stdin : createReadStream(values.log, { encoding: 'utf8' });
    console.log(JSON.stringify(await extract(input, values, jobs), null, 2));
  } catch (error) {
    console.error('aesop excerpt: ' + error.message);
    process.exitCode = 2;
  }
}

module.exports = { run };
