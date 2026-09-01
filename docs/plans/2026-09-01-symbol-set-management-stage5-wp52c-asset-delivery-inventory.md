# WP5.2c — asset delivery path inventory

Work package: `docs/plans/2026-09-01-symbol-set-management-stage5-implementation-plan.md`, §2 WP5.2c<br>
Scope: confirm every preview/download/thumbnail path serves bytes through an authorization-aware app route rather than a durable public object storage URL, per the programme plan's gate ("demotion remains disabled until objects are private or only short-lived, revocable authorization-derived URLs are issued").<br>
Method: static inventory of every route handler that reads object storage bytes and returns them to a client, plus a repository-wide search for any presigned/signed-URL pattern. No code change in this package — verification only.

## 1. Verdict

**PASS.** Every asset delivery route found in `backend/symgov_backend/routes/` reads object bytes server-side via `download_object_bytes()` (a SigV4-signed server-to-storage request, see `backend/symgov_backend/runtime.py:1168`) and streams them back as an HTTP `Response` body. No route returns a presigned URL, a raw storage endpoint URL, or an `object_key` that a client could turn into a direct storage request. A repository-wide search for `presign`, `signed_url`, `generate_presigned`, `public-read`, and `X-Amz-Signature` across `backend/symgov_backend`, `frontend/src`, and `scripts` returned no matches.

## 2. Inventory

| Route | File:line | Authorization | Reader inherits WP5.2a visibility floor? |
| --- | --- | --- | --- |
| `GET /published/symbols/{symbol_id}/preview` | `routes/published.py:729` | `published_router` mounted with `Depends(require_user)` at `app.py:113` (session-cookie auth for every route in the router) | Yes — via `_load_published_symbol_row` (`routes/published.py:327`), which queries `PUBLISHED_SYMBOLS_SQL` |
| `GET /published/symbols/{symbol_id}/supplemental-photos/{photo_id}/preview` | `routes/published.py:775` | Same router-level `Depends(require_user)` | Yes — same `_load_published_symbol_row` call before the `HannahPhotoCandidate` lookup |
| `GET /catalog/symbols/{symbol_ref}/thumbnail` | `routes/catalog.py:1404` | Per-route `Depends(require_catalog_scope(CATALOG_READ_SCOPE))` (API-key auth) | Yes — via `_catalog_symbol_preview_bytes` → `_load_catalog_symbol_row`, which queries `PUBLISHED_SYMBOLS_SQL` |
| `GET /catalog/symbols/{symbol_ref}/preview` | `routes/catalog.py:1425` | Same per-route `Depends(require_catalog_scope(CATALOG_READ_SCOPE))` | Yes — same `_catalog_symbol_preview_bytes` path |
| `POST /catalog/symbols/download` | `routes/catalog.py:1262` | Per-route `Depends(require_catalog_download_access)` — accepts either a session cookie or a catalog API key, both validated in-handler (`routes/catalog.py:198`) | Yes — resolves every requested symbol through `_load_catalog_symbol_row`, which queries `PUBLISHED_SYMBOLS_SQL` |
| `GET /workspace/review-cases/{review_case_id}/children/preview` | `routes/workspace.py:4718` | `workspace_router` mounted with `Depends(require_workspace_access)` at `app.py:117` (workspace membership/role check for every route in the router) | N/A — serves in-review (draft/intake) attachments by design, not published-symbol content; not a WP5.2 public-reader path |
| `GET /workspace/review-cases/{review_case_id}/source/preview` | `routes/workspace.py:4762` | Same router-level `Depends(require_workspace_access)` | N/A — same as above |

All seven routes were found by grepping `routes/*.py` for `preview`/`thumbnail`/`download` GET/POST handlers; none were excluded from this table.

## 3. How each route serves bytes (mechanism, not just presence of auth)

Every one of the seven routes above resolves an internal `object_key`, then calls:

```python
payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
```

`download_object_bytes` (`backend/symgov_backend/runtime.py:1168`) builds a SigV4-signed HTTP GET directly to the configured object-storage endpoint using server-held credentials, executes it server-side, and returns the raw bytes plus content type. The route handler then validates the bytes (`validate_stored_image` / equivalent) and returns them in a FastAPI `Response`. The client never receives the `object_key`, the storage endpoint, or any signature — only image/file bytes at an app-owned URL that itself required a fresh authorization check on that request. This is the "authorization-aware app route" shape the plan requires, not a durable public object URL.

## 4. `object_key` exposure check

Searched `routes/published.py` and `routes/catalog.py` for every use of `object_key` outside the seven preview/download handlers above (`HannahPhotoCandidate.object_key` filters, existence checks). None of these values are serialized into a JSON response — they are used only for server-side attachment lookups. No route returns a raw `object_key`, storage path, or bucket URL to the client.

## 5. Non-goal noted, not fixed, in this package

`GET /published/packs` (`routes/published.py:list_published_packs`) is a pack-level listing (`pk.status`/`pk.audience`/`count(pe.id)`) with no `governed_symbols` join and no per-symbol content in its response — it is not an asset delivery path and is out of WP5.2c's scope. It is noted here only because it independently restates `pk.status = 'published' AND pk.audience = 'public'` (a WP5.2a-adjacent observation, not a WP5.2c asset-delivery finding); see the WP5.2a/b completion note for detail. Recommend revisiting in WP5.6 if the whole-stage audit wants pack-level symbol counts to also reflect the visibility floor.

## 6. Conclusion

WP5.2c's acceptance criterion — a written inventory of every asset delivery path and how each authorizes access — is satisfied by §2 above. No short-lived signed-URL mechanism needs to be added: no durable public object URL was found anywhere in the codebase. WP5.2 (a, b, and c) is complete on this basis; WP5.3 may proceed under separate authorization.
