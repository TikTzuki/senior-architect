# HRM API used by the `hrm` CLI

Base: `$HRM_BASE_URL` (no `/api` suffix; the CLI strips one if present).
Auth: `Authorization: Bearer hrm_pat_…`. `Accept-Language: en|vi` (`HRM_LANG`) picks the language of `detail` in errors.

| CLI              | Method + path                                                                           | Token scope               | Notes                                                                                                       |
|------------------|-----------------------------------------------------------------------------------------|---------------------------|-------------------------------------------------------------------------------------------------------------|
| `whoami`         | `GET /api/me`                                                                           | read                      | `{id, email, displayName, role, departmentId}`                                                              |
| `positions`      | `GET /api/requisitions?status=OPEN`                                                     | read                      | full requisition objects; CLI trims them                                                                    |
| `search`         | `GET /api/applications/board?q=&requisition_id=&include_closed=`                        | read                      | one row per application (candidate × position)                                                              |
| `upload`         | `POST /api/candidates/from-cv` (multipart `file`, `requisition_id`, `source`, `if_new`) | write, role TA / HR admin | 201 `{candidate, applicationId, documentId, job}`; 409 = same file already in that position (`if_new=true`) |
| `review`         | `GET /api/candidates/{id}/evaluations`                                                  | read                      | per application: `cvDocumentId`, `review`, `match`, live `reviewJob` / `matchJob`                           |
| `review --run`   | `POST /api/cvs/{doc}/review[?force=true]`                                               | write, TA                 | 202 `{job}`; or `{job: null, review}` when one exists and not forced                                        |
|                  | `GET /api/cvs/{doc}/review`                                                             | read                      | 404 = not reviewed yet                                                                                      |
| `review --match` | `POST /api/applications/{app}/match`                                                    | write, TA                 | 202 `{job}`; 422 when the CV has no readable text                                                           |
| `job`            | `GET /api/ai/jobs/{id}`                                                                 | read                      | `status`: `queued` → `running` → `done` \| `error`; `result`, `error`                                       |

AI jobs run in the background on the HRM server. The CLI polls with backoff (1.5 s → 8 s) until
`done`/`error` or `--timeout` (default 180 s). On timeout it returns `{"pending": {job}}`, and
`hrm job ID --wait` picks it up later.

Extraction job result (`kind: extract_cv`): `{candidateId, name, merged, missingFields}`. When
`merged` is true, the uploaded placeholder candidate was deleted and its application moved to
the existing person with the same email.

## Exit codes

| Code | Meaning                                                                                          |
|------|--------------------------------------------------------------------------------------------------|
| 0    | success (for `upload`: no file **failed**; duplicates are not failures)                          |
| 1    | HRM returned an error; JSON `{error, status}` on stderr (for `upload`: at least one file failed) |
| 2    | usage / config problem (missing env vars, bad file)                                              |
| 3    | network: HRM unreachable                                                                         |

## Token rules (server side)

- Created only from a signed-in browser session: Settings → API tokens. A token can't create,
  list or revoke tokens.
- `read` tokens: GET only. `write` tokens: anything the owner's role allows.
- Tokens take the owner's **current** role. Deactivating the user blocks them immediately.
- Expiry: 30 / 90 / 180 / 365 days, no permanent tokens. HR admins can see and revoke anyone's.
- Only a SHA-256 hash is stored. A lost token can't be recovered, only replaced.
