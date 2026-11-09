# Security

animeDik is a small personal site, but it has accounts, sessions and file
uploads, so it is built as if someone will try to break it. This page
describes what stands in the way and how to report a problem.

## Reporting a problem

Please open an issue on GitHub **without** exploit details and ask for a
contact, or describe the problem privately if GitHub offers that for the
repository. This is a hobby project maintained by one person — there is no
bug bounty, but every real report is fixed and credited if you want.

## What stands in the way

**Passwords.** Only a fingerprint is stored: PBKDF2-HMAC-SHA256,
600,000 rounds, a random salt per account, constant-time comparison. At
least 10 characters, not from the list of the most common passwords. An
unknown login takes as long to check as a real one and gets the same
message, so logins cannot be collected by timing.

**Password guessing.** Two counters at once — per login and per address —
plus a rate limit in nginx on the sign-in route. During a lock even the
correct password does not get in.

**Sessions.** 256-bit random tokens; the database keeps only their
fingerprint, salted with the server secret (`SESSION_PEPPER`), so a stolen
database does not let anyone sign in. The cookie is `HttpOnly`,
`SameSite=Lax` and `Secure`. Changing a password closes every session;
"sign out everywhere" is one button.

**Two-factor sign-in.** TOTP codes from an authenticator app, single-use;
backup codes; the TOTP secret is stored encrypted with a key derived from
the server secret.

**Requests from other sites (CSRF).** Every request that changes data needs
a form mark signed with the server secret, and its `Origin` must match the
site itself — otherwise 403 before the body is even read.

**No sign-up at all.** There is no route that creates an account from the
browser — not "closed by a check", simply absent. The admin routes answer
404 (not 403) to everyone except an administrator, so their existence
cannot be detected.

**Guests.** A guest pass lives only in the process's memory for one hour;
not a single row is written to the database. Guest passes per address are
limited.

**The demo account** (optional, `DEMO_ACCOUNT`, demo mode only). One
button signs a visitor into a shared ordinary account without a password;
it never signs anyone in as an administrator. Every request from such a
session that is not a read gets 403 in one shared layer — only signing out
is allowed — so nobody can change its password, settings or library. The
button does not touch the wrong-password counters and is rate-limited per
address; its login and e-mail are never sent to the page.

**Uploads.** An avatar or banner is never stored as received: it is opened,
checked, its canvas size is read from the file header *before* decoding (a
tiny file claiming a gigantic canvas is rejected), cropped and re-encoded
as a clean JPEG. Only JPEG, PNG and WebP decoders are allowed; the upload
size is capped.

**SQL.** Every query is parameterised; no SQL is ever built from strings.

**Outgoing requests (SSRF).** Every address the server fetches — catalogue
data, posters, subtitles, video through the relay — must lead to the public
internet: local and private addresses are refused both by name and by the
actual IP at connection time, including after redirects. The video relay
only fetches addresses it signed itself.

**Browser headers.** A strict Content-Security-Policy (`script-src 'self'`),
no framing, no type sniffing, no referrer to other sites, camera,
microphone and location turned off.

**Visitor address.** `X-Forwarded-For` and similar headers are ignored
unless `TRUST_PROXY=1` is set and the app sits behind the bundled nginx
config, which overwrites them.

**The container.** Runs as a non-root user with a read-only filesystem; the
only writable place is the data volume. The app port is bound to
`127.0.0.1` and is reachable only through nginx.

**Dependencies.** Exact versions are pinned in `api/constraints.txt` and
checked with `pip-audit`; the check suites fail if a known-vulnerable
version comes back.

## What is up to you

- A long random `SESSION_PEPPER` (`openssl rand -hex 32`), and `chmod 600 .env`.
- A long administrator password.
- `COOKIE_SECURE=1` on a real site served over https.
- Keep the server's own updates on (unattended security updates, a monthly
  `docker compose build --pull`).
- To keep a private copy of the site off the public internet, put it behind
  Cloudflare Access (see [INSTALL.md](INSTALL.md), section 1.5).

## How it is checked

The security checks are part of the regular suites — see
[TESTING.md](TESTING.md). Most of them start from something that was
really broken once and must never come back: a forged visitor address, a
CSRF mark that compared the cookie with itself, a login with a line break
inside (`ad⏎min`) silently "repaired" into another account's login, a
database file readable by everyone.
