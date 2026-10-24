# MIT Election Data + Science Lab source files

Harvard Dataverse puts these two datasets behind a guestbook form: name, email, institution and position on every download. We download them by hand in a browser, so no personal details go into automation. Both are CC0 1.0, which lets us keep copies here. The electiondata pipelines read them from this folder.

| File to save here | Dataset | Version checked | License |
|---|---|---|---|
| `countypres_2000-2024.csv` | [County Presidential Election Returns 2000–2024](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/VOQCHQ), doi:10.7910/DVN/VOQCHQ | V20, 2026-02-25 | CC0 1.0 |
| `County Presidential Returns 2000-2024.md` | the codebook for the dataset above | V20 | CC0 1.0 |
| `1976-2024-house.tab` | [U.S. House 1976–2024](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2), doi:10.7910/DVN/IG0UN2 | V15, 2026-03-09 | CC0 1.0 |
| `codebook-us-house-1976–2024.md` | the codebook for the dataset above | V15 | CC0 1.0 |

## Updating

MEDSL publishes corrections a few times a year.

1. Open each dataset page.
2. Download each file, filling in the guestbook when asked. For `countypres_2000-2024.tab`, choose **Original File Format (CSV)**.
3. Replace the files here and commit them.

The pipeline notices that the content changed and publishes a new version.

Cite as: MIT Election Data and Science Lab, *County Presidential Election Returns 2000–2024* and *U.S. House 1976–2024*, Harvard Dataverse.
