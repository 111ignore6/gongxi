# Initial public-release security review

Review date: 2026-10-26. Scope: local HTTP server, browser UI, CLI credentials,
runtime management, backups, skill installation and the public Git file set.
This is an engineering review and regression test record, not a penetration-test
certification or a guarantee of Internet-facing security.

## Changes made before publication

- Browser write requests enforce local same-origin checks, reject malformed
  origins and cross-site Fetch Metadata, while retaining originless local CLI
  requests. Anonymous and cookie-only access cannot read workspace records.
- Application pages restrict scripts and connections to the same origin using
  response-header CSP, deny framing, disable referrers and use nosniff. Inline
  styles remain allowed because the current UI uses style attributes; inline
  scripts are not enabled.
- Messages and imported text are escaped on rendering. Imported credentials must
  match the current service. No analytics, external scripts, fonts or model SDKs
  are included.
- New browser invitation links use URL fragments and clear their address-bar
  secret before API calls. Legacy query invitations remain compatible; old URL
  requests may already exist in an external server's history or logs.
- Tab-scoped session storage preserves first-request enrollment recovery and
  drafts. Removing persistence would reintroduce identity-loss and refresh
  regressions without preventing same-page script access to in-memory tokens.
  Logout/revocation cleanup and the remaining browser trust boundary are
  documented in SECURITY.md and PRIVACY.md.
- Windows private-file ACLs are set before writing credential contents. Backup
  extraction and verification staging locations are protected before receiving
  database contents. Runtime logs and management files are restricted locally.
- API and management clients refuse redirects. Backups validate the fixed
  archive members, decompression bounds, checksums, required database columns and
  integrity; publication during restore does not overwrite a concurrently
  created target. Checksums are not authenticity signatures.
- Skill installation rejects symlink/junction escapes and incomplete or modified
  managed-file manifests rather than silently overwriting local files.
- Publication excludes databases, member and management credentials, backups,
  logs, installed-machine skill metadata, real screenshots and private historical
  acceptance records. The release gate inspects exact Git index blobs, not just
  the working tree, and can privately compare local credentials without printing
  their values.

## Evidence

Regression suites cover real loopback HTTP origin/authorization boundaries,
XSS-as-data responses, CSP headers, redirect refusal without contacting a second
server, pre-write permission ordering, restore race preservation, archive bounds,
installer preservation, runtime lifecycle and the original collaboration flows.
Tests use temporary data; generated client identities are not real independent
vendor-model participation claims.

On the review date, the complete local Windows/Python 3.13 suite passed:
39 tests in 76.615 seconds. The public screenshot was captured from a separate,
script-seeded demonstration workspace and visually reviewed; it contains no
real workspace conversations, credentials or personal paths. Its JPEG has no
EXIF metadata.

The pinned runtime requirements were checked with pip-audit 2.10.1 using
`--no-deps --disable-pip -r requirements-lock.txt` on the review date: no known
vulnerabilities were reported for the 13 explicitly pinned packages. The result
is time- and database-dependent; rerun the audit for future releases.

Windows/Python 3.13 local test results and Linux/Python 3.11 GitHub Actions checks
are separate evidence. A workflow being present does not mean its cloud run has
passed; consult the actual Actions status after publication.

## Remaining boundaries

The application is loopback-first and single-workspace. It does not isolate a
malicious same-user process or administrator, encrypt backups, authenticate a
claimed model vendor, erase content already copied by a revoked member, or
provide hardened public deployment. See SECURITY.md before deployment and use
private vulnerability reporting rather than posting secrets in issues.
