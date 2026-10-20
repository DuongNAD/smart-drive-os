# Releasing SmartDrive-OS

How a new version of SmartDrive-OS reaches PyPI, what the repository owner sets up once, and what to do when a release
goes wrong. The workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml), named "Release".

## 1. What a release produces

A release is one Git tag, `v<version>` (for example `v1.5.0`). Pushing it starts the workflow, which builds the source
distribution (sdist) and the wheel, tests the wheel on Linux, macOS and Windows and, once the owner approves, uploads
both files to PyPI as `smart-drive-os`. Users then install it with `pipx` or run it with `uvx`; the README has the
commands. The GitHub Release for the tag is written by hand afterwards (section 3, step 7): the workflow does not
create it.

Only a pushed `v*` tag publishes, and PyPI receives nothing until the owner approves the deployment in the protected
`pypi` environment. Every other run only builds and tests:

| What starts the workflow | What happens |
|---|---|
| Pushing a tag `v*` | Build and test, then upload to PyPI after the owner approves. |
| A push to a branch, or a pull request, that touches `pyproject.toml`, `README.md`, `LICENSE`, `smart_drive/**`, `packaging/**` or the workflow file | Build and test only. |
| A manual run (Actions → Release → Run workflow) | Build and test only. |

A run has three jobs:

| Job | What it does |
|---|---|
| `dist` ("Build sdist and wheel") | Builds the sdist and the wheel with `python -m build`, runs `twine check --strict` on them and, for a tag, fails unless the tag is `v` plus the `version` in `pyproject.toml`. The files are kept as the artifact `python-dist`. |
| `wheel-smoke` ("Install smoke test") | Installs the wheel into a fresh virtual environment on Linux, macOS and Windows with Python 3.9 and 3.13 and runs `packaging/smoke_test.py --wheel dist` in a sandboxed home: `--help`, `status --json`, `organize --dry-run` on a sample drive, `mcp register --workspace`, and the registered MCP entry started from another folder (`initialize` and `tools/list`). It then installs the pinned uv and pipx (`packaging/requirements-smoke.txt`) and runs the CLI and the MCP server through `uvx` (`--uvx dist`) and, on Python 3.13 only because the pinned pipx needs Python 3.11 or newer, through `pipx` (`--pipx dist`). |
| `publish-pypi` ("Publish to PyPI") | Pushed `v*` tags only, and only after `dist` and every `wheel-smoke` run passed. It runs in the environment `pypi`, so it waits for the owner's approval, and then uploads the files that `dist` built through `pypa/gh-action-pypi-publish` with Trusted Publishing (OpenID Connect) and PEP 740 attestations. It is the only job with `id-token: write`, and no token or password is stored anywhere. |

Every action in the workflow is pinned by commit SHA.

## 2. One-time setup (owner)

### PyPI

1. Sign in at https://pypi.org, or create the account at https://pypi.org/account/register/. PyPI requires two-factor
   authentication on every account, so turn it on first.
2. The project does not exist on PyPI yet, so add a *pending publisher*: open
   https://pypi.org/manage/account/publishing/, find "Add a new pending publisher", choose the GitHub tab and fill in:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `smart-drive-os` |
   | Owner | `DuongNAD` |
   | Repository name | `smart-drive-os` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   Then click Add.
3. Nothing else is needed on PyPI. The first successful upload from the workflow creates the project and turns the
   pending publisher into a normal trusted publisher. A pending publisher does not reserve the name, so publish the
   first release soon after adding it: if somebody registers `smart-drive-os` first, the pending publisher stops
   working.

No API token is created or stored anywhere. The job proves its identity to PyPI with a short-lived OpenID Connect token.

### GitHub environment `pypi`

In the repository open Settings → Environments → New environment, name it `pypi`, click Configure environment, and then:

- Tick **Required reviewers** and add yourself (DuongNAD). Leave "Prevent self-review" off: with a single maintainer it
  would stop you from approving the releases you tag yourself. Click Save protection rules.
- Under "Deployment branches and tags" choose Selected branches and tags → Add deployment branch or tag rule →
  Ref type: Tag → Name pattern `v*` → Add rule.
- Add no secrets and no variables. The environment needs none.

What this gives: nothing reaches PyPI until you approve the waiting deployment; a run from a branch, or from a tag that
does not start with `v`, cannot use the environment; and PyPI trusts only this repository, the workflow `release.yml`
and the environment `pypi`, so a copy of the workflow in another repository or under another name cannot publish.

### Optional: protect the release tags

Anyone with write access can push a `v*` tag and start a release run (it would still wait for your approval at `pypi`).
To leave creating, moving and deleting release tags to the repository admins, open Settings → Rules → Rulesets →
New ruleset → New tag ruleset and set:

- a name, for example "Release tags", and Enforcement status: Active;
- Bypass list → Add bypass → Repository admin;
- Target tags → Add target → Include by pattern → `v*`;
- the rules Restrict creations, Restrict updates and Restrict deletions.

Then click Create.

## 3. Cutting a release

Replace `<version>` with the new number (semantic versioning, for example `1.5.0`) and `<date>` with today's date.

1. **Bump the version everywhere.** Change it in `pyproject.toml`, in `smart_drive/__init__.py` (`__version__`), in
   `smart_drive/mcp/server.py` (`SERVER_VERSION`), in `server.json` (`version` and `packages[0].version`) and in the
   release badges of `README.md` and `README_VN.md` (`-v<version>-blue.svg` and `/releases/tag/v<version>`). In
   `CHANGELOG.md` move the entries under `## [Unreleased]` into a new `## [<version>] - <date>` section and update the
   link lines at the bottom (`[Unreleased]` compares `v<version>...main`; add `[<version>]` comparing
   `v<previous>...v<version>`). The tests fail when these disagree (`tests/test_version_consistency.py` and the checks
   of `server.json`).
2. **Test, commit, merge.** Run the tests and the gitignore audit:

   ```bash
   python -m unittest discover tests
   python -m smart_drive gitignore audit .
   ```

   Then commit with the message `chore(release): version <version>` and merge it into `main`.
3. **Optional local check** of what the workflow will build (`dist/` and `build/` are git-ignored):

   ```bash
   python -m pip install -r packaging/requirements-dist.txt
   python -m build
   python -m twine check --strict dist/*
   python packaging/smoke_test.py --wheel dist
   python -m pip install -r packaging/requirements-smoke.txt
   python packaging/smoke_test.py --uvx dist
   python packaging/smoke_test.py --pipx dist  # Python 3.11 or newer
   ```

4. **Tag `main`** and push the tag:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a v<version> -m "SmartDrive-OS <version>"
   git push origin v<version>
   ```

5. **Watch the run**: Actions → Release → the run for the tag. `dist` fails at once when the tag and the version in
   `pyproject.toml` differ (section 4 says how to take the tag back).
6. **Approve the upload.** When `dist` and `wheel-smoke` are green, the run waits at `publish-pypi`. Open the run page →
   Review deployments → tick `pypi` → Approve and deploy. Then check that https://pypi.org/project/smart-drive-os/ shows
   the new version and start it both ways from a clean environment:

   ```bash
   uvx smart-drive-os@<version> --help
   pipx install smart-drive-os==<version>
   smart-drive --help
   ```

7. **Write the GitHub Release.** Releases → Draft a new release → Choose a tag: `v<version>` → title `v<version>` →
   paste the `## [<version>]` section of `CHANGELOG.md` as the notes → Publish release. The wheel and the sdist are on
   PyPI already, so nothing has to be attached (the `python-dist` artifact of the run holds the same files). The PyPI
   project links to the Releases page as its changelog, so this is the page its users read.
8. **After the first release on PyPI**, remove the "after the first PyPI release" notes from the uvx and pipx options
   in the install section of `README.md` and `README_VN.md`. The PyPI page of that first release still shows them,
   since PyPI displays the README that was packaged with each release. To have them gone from that very page, remove
   them in step 1 of the first release instead; the README on GitHub then promises the install a few minutes before it
   works.

## 4. When something goes wrong

- **A version can be uploaded to PyPI only once.** PyPI never accepts a file name twice, not even after the release or
  the project was deleted. A version that is on PyPI and broken is fixed forward: bump the patch number and release
  again. A run that stopped while PyPI was receiving the files may have left one of the two files there; look at the
  file list at https://pypi.org/project/smart-drive-os/#files before doing anything else, and prefer a new patch
  version to patching up a half-published one.
- **Re-run, don't re-tag,** for a failure that has nothing to do with the code (a runner that timed out, a network
  error): on the run page open Re-run jobs → Re-run failed jobs (possible for 30 days after the run).
  `publish-pypi` waits for your approval again whenever it starts.
- **Stop a release** that has not reached PyPI: on the run page choose Review deployments → Reject (or Cancel
  workflow). Nothing is uploaded.
- **Take the tag back** only while nothing is on PyPI (`dist` or `wheel-smoke` failed, or you rejected the deployment).
  Delete the tag, fix the problem, merge, and tag again:

  ```bash
  git push --delete origin v<version>
  git tag -d v<version>
  ```

- **Yank** a broken release that went out: https://pypi.org/manage/project/smart-drive-os/releases/ → Options next to
  the version → Yank. Installers skip a yanked version unless someone pins exactly that version; the files stay, and
  the version number stays used. Then release the fixed version.
- **`invalid-publisher`** in the log of `publish-pypi` means PyPI got the run's token but no publisher matches it.
  Compare the repository, the workflow (`release.yml`) and the environment (`pypi`) that the log lists with the
  publisher you entered at https://pypi.org/manage/account/publishing/ (the project's own Publishing page, once the
  project exists).

## 5. Building without publishing

To check packaging changes without a tag, open Actions → Release → Run workflow, choose the branch and run it (the
button appears once `release.yml` is on the default branch). A push or a pull request that touches the packaging files
starts the same run by itself. Nothing is published, because `publish-pypi` runs only for a pushed `v*` tag. The sdist
and the wheel are the artifact `python-dist` at the bottom of the run page. The local equivalent is step 3 of
section 3.

## 6. Listing on the MCP Registry (optional)

The official MCP Registry (https://github.com/modelcontextprotocol/registry) is a public list of MCP servers that
clients and directories can read. `server.json` at the repository root describes SmartDrive-OS for it: the name
`io.github.DuongNAD/smart-drive-os`, the PyPI package `smart-drive-os`, the runtime hint `uvx` and the argument `mcp`.
Nothing has been published to the registry yet.

The registry checks that the PyPI package is yours by looking for `mcp-name: io.github.DuongNAD/smart-drive-os` in the
package description, which is `README.md`: the HTML comment on its second line carries it. So publish `server.json` only
after a PyPI release whose README has that line, with the registry's `mcp-publisher` command line tool installed (the
quickstart in the registry repository, under `docs/modelcontextprotocol-io`, shows how for each system). From the
repository root:

```bash
mcp-publisher login github
mcp-publisher publish
```

`mcp-publisher login github` starts a device login: open the address it prints, enter the code and sign in as DuongNAD;
the `io.github.DuongNAD/` namespace comes from that login. `mcp-publisher publish` then sends `server.json`. Repeat both
for every release, after the versions in `server.json` were bumped (section 3, step 1).

## 7. Code signing (only if a Windows `.exe` is ever shipped)

pip, pipx and uv install the wheel without a code-signing certificate, so a PyPI release needs none. Signing would
matter only if a Windows `.exe` were shipped (deferred, see A4 in `docs/UPGRADE_PLAN.md`): SmartScreen warns about
unsigned downloads, and none of these options removes the warning at once, since reputation builds over consecutive
releases signed with one identity. The options, as
[Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) lists them:

- **SignPath Foundation** (https://signpath.org/): free for qualifying open-source projects (MIT qualifies); the
  certificate is issued to SignPath Foundation, and the project page must carry a "Code signing policy" section.
- **Azure Artifact Signing** (formerly Trusted Signing): about US$9.99 a month, but individuals only in the USA and
  Canada, so not available to an individual in Vietnam today.
- **OV certificate** from a CA: about US$150-300 a year, worldwide; the key must live on a hardware token or HSM, so
  signing in GitHub Actions needs the CA's cloud signing service.
- **EV certificate**: US$400+ a year; since 2024 it no longer bypasses SmartScreen, so it is not worth the price.

## 8. Maintenance notes

- **README.md is the PyPI project description** (`readme = "README.md"` in `pyproject.toml`). PyPI cannot load a
  relative image such as `docs/banner.jpg`, so every image in it must use an absolute `https://` URL
  (`tests/test_install_docs.py` checks this). Relative links such as `[PRIVACY.md](PRIVACY.md)` work on GitHub and lead
  nowhere on the PyPI page. Keep the `mcp-name` comment on its second line: the MCP Registry finds it in the package
  description (section 6).
- **The tools are pinned**: `build` and `twine` in `packaging/requirements-dist.txt`, `uv` and `pipx` (which the smoke
  test runs the wheel with) in `packaging/requirements-smoke.txt`. They exist for the workflow and for local checks
  only; `dependencies = []` in `pyproject.toml` stays empty (`tests/test_compliance.py` checks that).
- **Actions are pinned by commit SHA** in `release.yml`. To update one, change the SHA and the version comment beside
  it together, and take the SHA from the release tag of the action.
- **This guide has a test.** `tests/test_install_docs.py` fails when the guide stops naming `release.yml`, the
  environment `pypi` and the other terms the setup depends on, or names a file that does not exist. Change the guide in
  the same commit as the workflow.
