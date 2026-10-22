"""
Test suite for tools/readme_lint.py

Each case builds a throwaway repo root at runtime (README.md plus an optional
docs/img SVG) and drives the real gate as a subprocess. Fixtures are assembled
from parts at runtime so this file never contains a bare Mermaid fence itself.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
GATE = os.path.join(REPO, 'tools', 'readme_lint.py')

FENCE = '`' * 3
MERMAID_OPEN = FENCE + 'mermaid'
MERMAID_BODY = 'flowchart LR\n  A["alpha"] --> B["beta"]'
IMAGE_LINE = ('![Kernel boundary](https://cdn.jsdelivr.net/npm/@matt82198/aesop@0.10.4'
              '/docs/img/kernel-boundary.svg)')
RAW_IMAGE_LINE = ('![Kernel boundary](https://raw.githubusercontent.com/matt82198/aesop'
                  '/main/docs/img/kernel-boundary.svg)')


def _sha(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _svg(body, digest=None):
    if digest is None:
        digest = _sha(body)
    return ('<!-- mermaid-source-sha256: ' + digest + ' -->\n'
            '<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>\n')


def _fence_block():
    return MERMAID_OPEN + '\n' + MERMAID_BODY + '\n' + FENCE + '\n'


def _details_fence():
    return ('<details><summary>Mermaid source</summary>\n\n'
            + _fence_block() + '\n</details>\n')


class ReadmeLintTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='readme-lint-')
        os.makedirs(os.path.join(self.root, 'docs', 'img'))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)

    def _run(self):
        proc = subprocess.run(
            [sys.executable, GATE, '--root', self.root],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_bare_fence_outside_details_fails(self):
        self._write('docs/img/kernel-boundary.svg', _svg(MERMAID_BODY))
        self._write('README.md', 'intro\n\n' + IMAGE_LINE + '\n\n' + _fence_block())
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('not inside a <details> block', out)

    def test_fence_in_details_with_current_svg_passes(self):
        self._write('docs/img/kernel-boundary.svg', _svg(MERMAID_BODY))
        self._write('README.md', 'intro\n\n' + IMAGE_LINE + '\n\n' + _details_fence())
        code, out = self._run()
        self.assertEqual(code, 0, out)

    def test_stale_hash_fails(self):
        self._write('docs/img/kernel-boundary.svg',
                    _svg(MERMAID_BODY, digest=_sha('an older diagram')))
        self._write('README.md', IMAGE_LINE + '\n\n' + _details_fence())
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('stale SVG', out)

    def test_missing_image_fails(self):
        self._write('docs/img/kernel-boundary.svg', _svg(MERMAID_BODY))
        self._write('README.md', 'no image here\n\n' + _details_fence())
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('no image line above mermaid fence', out)

    def test_image_pointing_at_missing_svg_fails(self):
        self._write('README.md', IMAGE_LINE + '\n\n' + _details_fence())
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('SVG missing', out)

    def test_raw_githubusercontent_image_fails(self):
        self._write('docs/img/kernel-boundary.svg', _svg(MERMAID_BODY))
        self._write('README.md', RAW_IMAGE_LINE + '\n')
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('raw.githubusercontent.com', out)

    def test_raw_githubusercontent_plain_link_fails(self):
        self._write('README.md', 'see [logo](https://raw.githubusercontent.com/o/r/main/x.png)\n')
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('raw.githubusercontent.com', out)

    def test_github_blob_markdown_image_fails(self):
        self._write('README.md', '![clip](https://github.com/matt82198/aesop/blob/main/assets/x.gif)\n')
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('blob/raw', out)

    def test_github_raw_html_img_src_fails(self):
        self._write('README.md', '<img src="https://github.com/matt82198/aesop/raw/main/x.png" alt="x">\n')
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn('blob/raw', out)

    def test_github_blob_plain_link_is_not_an_image(self):
        self._write('README.md', '[License](https://github.com/matt82198/aesop/blob/main/LICENSE)\n')
        code, out = self._run()
        self.assertEqual(code, 0, out)

    def test_npm_cdn_image_passes(self):
        self._write('docs/img/kernel-boundary.svg', _svg(MERMAID_BODY))
        self._write('README.md', IMAGE_LINE + '\n')
        code, out = self._run()
        self.assertEqual(code, 0, out)

    def test_repo_readme_passes(self):
        proc = subprocess.run(
            [sys.executable, GATE, '--root', REPO],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
