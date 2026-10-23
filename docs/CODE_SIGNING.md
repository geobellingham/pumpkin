# Code signing policy

Pumpkin's Windows downloads are signed so Windows can show who made them.

Free code signing is provided by [SignPath.io](https://signpath.io), certificate by the
[SignPath Foundation](https://signpath.org).

## What is signed

Only builds made by this repository's GitHub Actions workflow (`.github/workflows/release.yml`) from the public
source at https://github.com/geobellingham/pumpkin. Nothing built on a personal computer is signed.

## Who does what

| Role | Who |
| --- | --- |
| Author (writes the code) | [geobellingham](https://github.com/geobellingham) and contributors whose pull requests are merged |
| Reviewer (reviews changes before they are merged) | [geobellingham](https://github.com/geobellingham) |
| Approver (approves each signing request) | [geobellingham](https://github.com/geobellingham) |

The maintainer's GitHub account uses two-factor authentication. Contributions from others are reviewed before they are
merged, and only merged code is built and signed.

## Privacy

Pumpkin runs on your computer and keeps your chats, files and settings there. It only reaches the internet when you
use something that needs it: downloading models or the engines from Hugging Face and GitHub, web search for a model
that asks for it, and the check for a newer Pumpkin (Settings, can be turned off). Nothing else is sent anywhere.

## Reporting a problem

If a signed build looks wrong, open an issue or follow [SECURITY.md](../SECURITY.md).
