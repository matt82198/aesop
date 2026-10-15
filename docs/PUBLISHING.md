# Publishing Aesop to npm

This guide explains how to publish new releases of Aesop to npm using GitHub Actions with OIDC trusted publishing (zero tokens, zero OTP prompts).

## Overview

The publish workflow (`.github/workflows/publish.yml`) is triggered automatically when you **publish a GitHub Release**. It uses npm's OIDC trusted publishing feature to authenticate with npm — no tokens, no 2FA/passkey prompts, no OTP required.

## One-Time Setup on npmjs.com

### Step 1: Add a Trusted Publisher

You must configure GitHub as a trusted publisher for `@matt82198/aesop` on npmjs.com. This tells npm to allow publishes from the GitHub Actions workflow without a token.

**Steps:**

1. Go to **[npmjs.com/package/@matt82198/aesop/settings](https://www.npmjs.com/package/@matt82198/aesop/settings)** (substitute your actual username/package name).
   - If the package doesn't exist yet, you'll need to create it first (see bootstrap caveat below).

2. Click **Settings** → **Access** (or **Publishing** on newer UI).

3. Scroll to **Trusted Publishers** (or **Publishing from CI/CD**).

4. Click **Add a Trusted Publisher** → Select **GitHub Actions**.

5. Enter these values:
   - **Organization:** `matt82198` (your GitHub username or org)
   - **Repository:** `aesop`
   - **Workflow:** `publish.yml`

6. Click **Save** or **Add**.

npm will now allow publishes from the `publish.yml` workflow in the `aesop` repo without a token.

### Step 2: Verify npm Version

The workflow automatically ensures npm >= 11.5.1 (OIDC requirement). If you're publishing locally (not recommended), confirm:

```bash
npm --version  # Should be 11.5.1 or higher
```

## Publishing a Release

### Via GitHub Web UI (Recommended)

1. Go to your repo's **Releases** page.
2. Click **Draft a new release**.
3. **Tag version:** Enter a version (e.g., `0.1.0`, `0.1.0-beta.1`).
4. **Release title:** E.g., "Aesop 0.1.0".
5. **Description:** Add release notes (optional).
6. Click **Publish release**.

The workflow will automatically trigger and:
- Check out your code.
- Run secret scanning to ensure no credentials are accidentally published.
- Determine the dist-tag:
  - If version contains a hyphen (prerelease, e.g., `0.1.0-beta.1`): `npm publish --tag beta`
  - Otherwise (stable): `npm publish --tag latest`
- Publish to npm with no token or passkey prompt.

### Via Git CLI

```bash
git tag 0.1.0 -a -m "Release Aesop 0.1.0"
git push origin 0.1.0
# Then go to GitHub Releases and click "Create release from tag"
# (Or use gh CLI: gh release create 0.1.0 --generate-notes)
```

## Dist-Tag Logic

The workflow automatically selects a dist-tag based on the version in `package.json`:

- **Prerelease versions** (contain hyphen):
  - `0.1.0-beta.1` → `npm publish --tag beta`
  - `0.1.0-rc.1` → `npm publish --tag beta`
  - `1.0.0-alpha` → `npm publish --tag beta`

- **Stable versions** (no hyphen):
  - `0.1.0` → `npm publish --tag latest`
  - `1.0.0` → `npm publish --tag latest`

Users installing with `npm install @matt82198/aesop` will get the latest stable version by default. Beta versions can be installed with `npm install @matt82198/aesop@beta`.

## Secret Scanning

Before every publish, the workflow runs:

```bash
python3 tools/secret_scan.py .
```

This scans the entire package tree for credentials (API keys, tokens, PEM files, etc.). **If any are found, the publish will fail.** This is a safety gate — credentials should never appear in published npm packages.

If a false positive occurs (e.g., pattern documentation), add the pragma to the file's first 10 lines. The pragma only softens doc-shaped rules (generic_secret_assignment, env_access); a credential-shaped `KEY_NAME=value` line still blocks, so use a placeholder value in docs instead:

```python
# secretscan: allow-pattern-docs
```

See `tools/secret_scan.py` for full pragma documentation.

## Bootstrap Caveat: First Publish

**Important:** npm trusted publishing requires the package to already exist on npmjs.com before you can attach a trusted publisher.

- **If the package does NOT yet exist:** You'll need a **one-time bootstrap publish** using either:
  1. An npm **Automation token** (kept in a personal CI/CD secret, used once, then deleted).
  2. The npm web flow (manual login with passkey, generate temporary token).

  After the first publish, the package exists and the trusted publisher is active for all future releases.

- **If the package already exists:** Skip this step. The workflow will publish directly with no token or passkey.

**To bootstrap (one-time only):**

1. Generate an npm Automation token at [npmjs.com/settings/tokens](https://npmjs.com/settings/tokens) (API type, pub access).
2. Add it as a GitHub secret (e.g., `NPM_TOKEN`).
3. Update the workflow `env` section to use it:
   ```yaml
   env:
     NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
   ```
4. After the first publish succeeds, **remove the secret from the workflow** (revert to `NODE_AUTH_TOKEN: ${{ secrets.npm_token || '' }}`).
5. The trusted publisher is now active for future releases.

## Troubleshooting

### Publish fails: "No permission to publish"

→ **Trusted publisher not configured.** Go to npmjs.com settings and add it (step 1 above).

### Publish fails: "npm: command not found"

→ Shouldn't happen (actions/setup-node handles this). Check Node 22 is installed.

### Publish fails: "Secret scan found credentials"

→ Run `python3 tools/secret_scan.py .` locally to identify the leak. Remove or redact it, re-commit, then re-release.

### I'm using a passkey-only account and get OTP prompts

→ That's exactly why we use OIDC. Trusted publishing bypasses passkey+OTP entirely. Ensure the workflow and trusted publisher are correctly configured.

## References

- [npm OIDC Trusted Publishing](https://docs.npmjs.com/generating-and-authenticating-with-deploy-tokens#authenticating-with-oidc)
- [GitHub Actions: Setting up Node.js](https://github.com/actions/setup-node)
- [Secret Scanning Tool](../tools/secret_scan.py)
