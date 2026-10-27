# Releasing Aesop

How a release is cut and published. This page is for the maintainer. It is public so that anyone can see how
the package that reaches npm is produced and what is checked on the way.

Publishing itself is done by a GitHub Actions workflow, not by hand. The one-time npm setup and the
troubleshooting notes are in [PUBLISHING.md](./PUBLISHING.md); this page does not repeat them.

## What publishes a release

The workflow `.github/workflows/publish.yml` runs when a GitHub Release is **published**
(`.github/workflows/publish.yml:3-5`). Pushing a tag does not publish anything, and nothing in
`.github/workflows/` creates the Release from a tag, so the Release is created by hand.

<!-- CMD: sed -n 3,5p .github/workflows/publish.yml
OUTPUT:
on:
  release:
    types: [published]
-->
<!-- CMD: grep -ln "gh release\|create-release\|action-gh-release" .github/workflows/*
OUTPUT: (no output) -->

The workflow then runs these steps, in order (`.github/workflows/publish.yml`):

1. Check out the code and set up Node from `.nvmrc`.
2. Install a pinned npm that supports OIDC trusted publishing. No npm token is stored in the repository.
3. Run `python3 tools/secret_scan.py .` over the tree. A finding stops the run before any publish.
4. Choose the npm dist-tag from the version in `package.json`: `-rc.` gives `rc`, `-beta.` gives `beta`,
   `-alpha.` gives `alpha`, any other suffix gives `next`, and a plain version gives `latest`.
5. Run `python3 tools/readme_lint.py` so a README with a bare Mermaid fence does not reach the npm page.
6. Run `npm publish --access public --tag <tag>`.

One thing to know about step 6: it is marked `continue-on-error`, and a later step prints diagnostics and
fails the job if the publish step failed. A red run therefore means the publish did not happen. Check the
registry rather than assuming (see "Verify" below).

## Cut a release

Work on a branch, never on `main`.

1. **Update the changelog.** Move the entries under the Unreleased heading of [CHANGELOG.md](../CHANGELOG.md)
   into a new `## [x.y.z] - YYYY-MM-DD` section. Update [RELEASE-NOTES.md](../RELEASE-NOTES.md) from that section.
2. **Bump the version.** Set the version in `package.json` (and `package-lock.json`, which carries it too, at
   lines 3 and 9). `npm version <patch|minor|prerelease> --no-git-tag-version` edits both without
   creating a tag.
3. **Run the tests.** `npm run test:all`. Do not publish from a tree where any harness is red.
4. **Open a pull request** and merge it once CI is green.
5. **Tag the merge commit** and push the tag: `git tag vX.Y.Z <sha>` then `git push origin vX.Y.Z`.
6. **Publish a GitHub Release** for that tag on the Releases page. Mark pre-releases as pre-release. This is the
   step that starts `publish.yml`.

The version scheme is stated in `CHANGELOG.md:8`: stable releases are `0.x.y`, and `-beta.N` and `-rc.N`
are pre-releases. The workflow also maps `-alpha.` to its own dist-tag.

## Verify

After the workflow finishes:

```
npm view @matt82198/aesop version
npm view @matt82198/aesop dist-tags
```

The first should print the version you released. The second should show it under the tag the workflow chose.
For the current release:

<!-- CMD: node -e "console.log(require('./package.json').version)"
OUTPUT: 0.11.0 -->

`package.json` reads `0.11.0`, and the GitHub release list shows `v0.11.0` as Latest
(`gh release list --repo matt82198/aesop --limit 1`).

If the registry does not show the version, open the failed run, read the "Diagnose publish failure" step, and
follow [PUBLISHING.md](./PUBLISHING.md).

## Check the shipped package

The package contents are set by the `files` list in `package.json`. To see what a tarball would contain without
publishing:

```
npm pack --dry-run
```

<!-- CMD: npm pack --dry-run 2>&1 | grep -c receipt_pubkeys
OUTPUT: 1 -->

The tarball includes `tools/receipt_pubkeys/*.pub`, which the provenance receipt needs in order to verify
offline (`package.json`, `files` list; `CHANGELOG.md:32`).

## Roll back

A published npm version cannot be replaced. If a release has a serious defect:

1. Deprecate it so installs warn: `npm deprecate @matt82198/aesop@X.Y.Z "reason; use X.Y.Z+1"`.
2. Edit the GitHub Release to say so, or mark it as a pre-release.
3. Ship a fixed patch release through the steps above.

Deleting a tag does not unpublish anything from npm.

## Who can do this

Only the maintainer. The publish workflow authenticates to npm as this repository through OIDC trusted
publishing, set up once on npmjs.com (see [PUBLISHING.md](./PUBLISHING.md)). A fork cannot publish this package.
