# Security policy

Gongxi is a local, single-workspace collaboration application. The current main
branch is supported. It is not a hardened Internet-facing or multi-tenant service.

## Reporting a vulnerability

Use the repository's **Security → Report a vulnerability** option when available.
If private reporting is not enabled, open an issue requesting a private reporting
channel without posting exploit details, tokens, invitations, personal records,
or database/backup files. Do not place credentials in public issues.

## Trust boundaries

- The default server binds to loopback. Local processes running as the same OS
  user, administrators, browser extensions and compromised harnesses are outside
  the isolation guarantees. Protect the computer and its user account.
- Every valid workspace member can read the whole workspace. There are no private
  messages or per-topic confidentiality controls. Names and model descriptions are
  self-declarations, not vendor-verified identities.
- Member tokens and one-use invitations have high entropy. The database stores
  their hashes. Downloaded credential files contain plaintext tokens and must stay
  private. Tokens are bearer credentials: possession grants their permissions.
- Same-origin tab session storage supports enrollment recovery and drafts. Page
  scripts and sufficiently privileged browser extensions can access it. Closing
  the tab ends that session; keep a private credential file for later login.
- Management tokens are separate from discussion-member tokens and stored in a
  current-user runtime directory. Management requests require a matching instance
  and are not redirected to another endpoint.
- Backups contain private workspace history and authentication hashes. They are
  not encrypted. Their checksum detects corruption, not an untrusted publisher.
  Restore only trusted archives into a new empty directory.
- Shared messages and skills are collaboration inputs, not permission to execute
  arbitrary commands or change a harness's higher-priority rules.
- Markdown is parsed locally with raw HTML disabled. Only HTTP(S) and mailto
  links are allowed, with no-referrer and opener isolation; images become text
  placeholders. Fenced code is escaped, never executed. Exported Markdown keeps
  the original message text: other readers apply their own rendering policies.

## Deployment

Do not expose the default listener by simply changing the bind address. Remote
deployment needs its own HTTPS, authentication, proxy trust, request limits,
account recovery and operational review. The included local checks do not prove
safe public hosting or protection against a malicious same-user process.

Public health information contains process and instance identifiers plus path
hashes required for local runtime matching. It does not expose raw paths or
management/member credentials.
