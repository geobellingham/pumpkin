# Security

## Reporting a problem

Please report security problems privately through GitHub:
[open a private advisory](https://github.com/geobellingham/pumpkin/security/advisories/new).
Don't open a public issue for them.

Say what you found, how to reproduce it, and which version and system you used. You'll get an answer
within a week. Once a fix is out, you'll be credited in the release notes unless you'd rather not be.

## Supported versions

Only the latest release gets fixes. Pumpkin tells you when a new version is out, and Settings > Update
installs it.

## What counts

Anything that lets someone who doesn't have your access key reach Pumpkin or the model server, or lets
anyone do more than intended. For example:

- getting past the sign-in, the device key, or the check that blocks unknown domain names
- a page or file the AI made running as part of Pumpkin
- llama.cpp's command tools reaching outside the Docker sandbox
- a paired device getting anything other than answers
- the installer or the update button running files that weren't checked

Known limits are listed under [Security in the README](README.md#security), such as home-network mode
being unencrypted and Docker access being equal to root. Reports that improve on them are welcome too.

## How releases are built

Releases are built by GitHub Actions from a tagged commit. The workflows use actions pinned to exact
commits, and the downloads list their SHA-256 checksums, which the installer and the update button check.
