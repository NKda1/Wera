# Security

WERA is a public repository that handles API keys, mailbox credentials and CVs. This page says how to report a problem and what to do if a key leaks.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository. Include what you found, how to reproduce it, and what an attacker could do with it.

You should get a reply within 7 days. Once a fix ships, we'll credit you in the release notes unless you'd rather stay anonymous.

## What's in scope

- A key, token or password reaching the repository, its history, the browser bundle, a log line, an error page or CI output
- A way for content in a job-alert email or a scraped page to make WERA act (send a request, call a tool, change data)
- Any request WERA makes to `linkedin.com`
- Any path that submits an application or sends a message without a human pressing the button
- Contact details or work-authorisation data reaching a model prompt

## If a key leaks

Treat any key that was pushed, even for a moment, as compromised. Forks and caches keep old history, so deleting the commit is not enough.

1. **Revoke the key at the provider now.** This is the only step that actually stops misuse.
   - OpenRouter: Settings → Keys → delete the key
   - Firecrawl: Dashboard → API keys → revoke
   - Mailbox app password: revoke it in your mail provider's security settings
2. Create a new key, put it in your secret store or `.env.local`, and restart or redeploy.
3. Check the provider's usage logs for the time the key was exposed.
4. Remove the key from git history with `git filter-repo`, force-push, and ask anyone with a fork to re-clone.
5. Add a short note to this file's log below (date, which key, how long it was exposed, what was done).

## Controls in place

- `.gitignore` excludes `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key` and `secrets/`.
- gitleaks runs before each commit (`uvx pre-commit install`, once per clone) and over the full history in CI.
- CI fails if any key-like string is found in the built browser bundle, or if any `NEXT_PUBLIC_` variable name contains `KEY`, `SECRET`, `TOKEN` or `PASSWORD`.
- CI on pull requests from forks gets no secrets, and no workflow uses `pull_request_target`.
- Repository settings: turn on **secret scanning** and **push protection** (Settings → Code security).

## Leak log

None so far.
