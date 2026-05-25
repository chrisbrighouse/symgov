# Symgov Task List

## Current Notes

- Scott manual source-discovery control on the submissions page is now visible by default because admin mode defaults to `true` until login/auth exists.
- Frontend build verified successfully after the submissions-page change.
- Header navigation now uses exact route matching so only the active screen highlights in the top bar.
- Submissions now live on `/submit` with `/standards/submit` redirecting there so Standards no longer inherits the submissions screen state.
- The rebuilt static bundle has been published into `/data/symgov`, and the static site entry now points at the new hashed assets.
- Scott source search now retries alternate request shapes so the button can work against either the newer or legacy backend contract.
- Scott source discovery already persists a raw runtime log, a structured JSON report, and an agent run trace that can be summarized later.
