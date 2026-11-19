# Contributing

Thanks for taking the time. Issues and pull requests are welcome, especially fixes to the template, corrections to HISTORY.md where a date or fact is wrong, and bug reports or tests for the Hugin code.

## Sign your commits (DCO)

Every commit needs a sign-off under the Developer Certificate of Origin, version 1.1. The full text is at <https://developercertificate.org/>. By signing off you state that you wrote the change, or otherwise have the right to submit it under this repository's licence (MIT).

Sign off by adding a trailer to the commit message:

```
Signed-off-by: Your Name <you@example.com>
```

`git commit -s` adds it for you. The name and email in the `Signed-off-by` line must match the commit's author. Every commit in a pull request needs this sign-off before it is merged; `git commit --amend -s` or `git rebase --signoff` adds a missing one.

## What fits here

The snapshots in `snapshots/` are historical records. They are redactions of real files at fixed dates, so changes to their content are out of scope. If you find a missed redaction or anything else that looks private, do not open an issue or pull request: report it privately as described in [SECURITY.md](SECURITY.md). The template is meant to stay generic: changes that tie it to one host, one vendor or one setup belong in your own copy.

For the Hugin code, run the tests before opening a pull request:

```
python -m pip install -e "hugin[test]"
python -m pytest hugin/tests
```

## Licence

By contributing you agree that your contribution is licensed under the MIT License in [LICENSE](LICENSE).
