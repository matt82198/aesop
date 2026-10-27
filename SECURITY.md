# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, use GitHub's private security advisory feature:

1. Go to [Security > Advisories](https://github.com/matt82198/aesop/security/advisories)
2. Click "New draft security advisory"
3. Describe the vulnerability with steps to reproduce
4. The maintainer will be notified privately and can work with you on a fix

Private vulnerability reporting is enabled on this repository, so you can also file a report directly at https://github.com/matt82198/aesop/security/advisories/new.

## Supported versions

Only the latest release line receives security updates. The current line is **0.11.x**.

<!-- CMD: node -e "console.log(require('./package.json').version)"
OUTPUT: 0.11.0 -->

| Version | Status             |
|---------|--------------------|
| 0.11.x  | Supported          |
| < 0.11  | Not supported      |

The version comes from `package.json` (`"version": "0.11.0"`, line 3). Check the latest published version with
`npm view @matt82198/aesop version`; if it differs from the table, the table is out of date and the npm
version is the one to use.

## Policy

- Security patches will be released as soon as possible after confirmation
- Public disclosure will occur after a patch is released
- If a vulnerability is already public, we will prioritize a fix over coordinated disclosure

## What the shipped code does for security

These are the checks that exist in this repository, not a guarantee:

- A secret scan runs in the pre-push policy and before an npm publish (`hooks/pre-push-policy.sh:796`,
  `.github/workflows/publish.yml`, step "Scan for secrets before publish").
- The CI workflows include CodeQL and OpenSSF Scorecard (`.github/workflows/codeql.yml`,
  `.github/workflows/scorecard.yml`).
- Changes to gate code and workflows are routed to the maintainer for review (`.github/CODEOWNERS:9-19`).
  Required code-owner approval is not turned on (`.github/CODEOWNERS:3-5`).

## Advisory notice

The dependency advisory for `undici` (Dependabot #21) is resolved on main: `undici` is bumped to 8.12.3 in the `ui/web` lock file by [PR #1312](https://github.com/matt82198/aesop/pull/1312).
