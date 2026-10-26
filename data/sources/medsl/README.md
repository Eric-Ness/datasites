# MIT Election Data + Science Lab source files

Harvard Dataverse puts these two datasets behind a guestbook form, which asks for a name, email, institution and position on every download. We download them by hand in a browser, so no personal details go into automation. Both are CC0 1.0, which lets us keep copies here. The electiondata pipelines `county-president` and `house-results` read them from this folder.

| File here | Dataverse file | Dataset | Version | License |
|---|---|---|---|---|
| `countypres_2000-2024.csv` | `countypres_2000-2024.tab`, original format (CSV) | [County Presidential Election Returns 2000–2024](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/VOQCHQ), doi:10.7910/DVN/VOQCHQ | V20, 2026-02-25 | CC0 1.0 |
| `County Presidential Returns 2000-2024.md` | same name | the codebook for the dataset above | V20 | CC0 1.0 |
| `1976-2024-house.csv` | `1976-2024-house.tab` (a CSV despite its name) | [U.S. House 1976–2024](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2), doi:10.7910/DVN/IG0UN2 | V15, 2026-03-09 | CC0 1.0 |
| `codebook-us-house-1976–2024.md` | same name | the codebook for the dataset above | V15 | CC0 1.0 |

The files are kept byte for byte as Dataverse serves them, with CRLF line endings; `.gitattributes` stops git from changing them.

## How the pipelines check them

Dataverse's metadata API needs no guestbook response. On every run, the pipeline asks it for the dataset's latest version, then checks two things:
- **The version.** It must still be the one the contract's attribution cites. If MEDSL has published a newer version, the run stops with an error until a person updates the files.
- **The checksum.** The MD5 of the copy here must equal Dataverse's checksum for that file, so a damaged or stale copy stops the run.

`scripts/verify/election.mjs` makes the same checks before it re-derives the published figures.

## Updating

MEDSL publishes corrections a few times a year. When a run stops because of a new version:

1. Open the dataset page and read what changed.
2. Download each file, filling in the guestbook when asked. For `countypres_2000-2024.tab`, choose **Original File Format (CSV)**.
3. Save each one here under the name in the first column.
4. Update the citation:
   - **Where:** `source.attribution` in `contracts/sites/electiondata/datasets/county-president.yaml` or `house-results.yaml`.
   - **Text:** the citation shown on the dataset page.
   - **Version:** bump the contract's `version`.
5. Run `uv run datasites run electiondata --target local` and `node scripts/verify/election.mjs`, then commit.

Cite as: MIT Election Data and Science Lab, *County Presidential Election Returns 2000–2024* and *U.S. House 1976–2024*, Harvard Dataverse.
