"""Object keys in the data bucket. apps/api/src/types.ts reads the same layout; change both together."""


def catalog_key(site: str) -> str:
    return f"sites/{site}/catalog.json"


def manifest_key(site: str, dataset: str) -> str:
    return f"sites/{site}/datasets/{dataset}/manifest.json"


def version_key(site: str, dataset: str, version: str) -> str:
    return f"sites/{site}/datasets/{dataset}/versions/{version}.json"


def blob_key(sha256: str) -> str:
    return f"blobs/sha256/{sha256[:2]}/{sha256}"


def state_key(site: str, dataset: str) -> str:
    return f"ops/state/{site}/{dataset}.json"


def raw_key(source_id: str, sha256: str, ext: str) -> str:
    return f"raw/{source_id}/{sha256}.{ext}"
