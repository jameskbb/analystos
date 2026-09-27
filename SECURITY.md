# Security policy

AnalystOS is a personal, open-source project, built largely with AI assistance. It has not had
an independent security audit, and there is no support agreement, response-time commitment or
guarantee that a reported issue will be fixed.

It is designed to run locally: the API and web app bind to `127.0.0.1` by default, and
production mode refuses to start without a secret key. Do not expose it to the internet or point
it at sensitive data without your own review. [docs/security.md](docs/security.md) describes the
deployment model, the controls that exist and the known gaps.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/jameskbb/analystos/security/advisories/new)
rather than in a public issue. Include the affected component, steps to reproduce and the impact
you observed.

Reports are read and handled on a best-effort basis. Only the latest commit on `main` is
considered; there are no maintained release branches.

The software is provided under the [MIT License](LICENSE), "as is", without warranty of any kind.
