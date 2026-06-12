#!/usr/bin/env python3
"""
Borra llaves expiradas de GitHub Pages y sobreescribe los manifests
en el registry de Ollama con un blob dummy (borrado efectivo).
"""
import base64, hashlib, json, os, struct
from datetime import datetime, timezone
from pathlib import Path
import httpx

REGISTRY  = "https://registry.ollama.ai/v2"
AUTH_URL  = "https://registry.ollama.ai/v2/token"
API_KEY   = os.environ.get("OLLAMA_API_KEY", "")


def gguf_dummy() -> bytes:
    def kv(k, v):
        kb, vb = k.encode(), v.encode()
        return struct.pack("<Q", len(kb)) + kb + struct.pack("<I", 8) + struct.pack("<Q", len(vb)) + vb
    return (b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) +
            struct.pack("<Q", 1) + kv("general.architecture", "llama"))


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_token(namespace: str, model: str) -> str:
    r = httpx.get(AUTH_URL,
        params={"scope": f"repository:{namespace}/{model}:pull,push",
                "service": "registry.ollama.ai"},
        headers={"Authorization": f"Bearer {API_KEY}"}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]


def upload_blob(data: bytes, digest: str, token: str, ns: str, model: str):
    hdrs = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as c:
        r = c.head(f"{REGISTRY}/{ns}/{model}/blobs/sha256:{digest}", headers=hdrs)
        if r.status_code == 200:
            return  # ya existe
        r = c.post(f"{REGISTRY}/{ns}/{model}/blobs/uploads/", headers=hdrs)
        loc = r.headers["Location"]
        sep = "&" if "?" in loc else "?"
        c.put(f"{loc}{sep}digest=sha256:{digest}", content=data,
              headers={**hdrs, "Content-Type": "application/octet-stream"}, timeout=30)


def overwrite_manifest(namespace: str, model: str, tag: str = "latest"):
    """Sobreescribe el manifest remoto con un blob dummy de 69 bytes."""
    dummy = gguf_dummy()
    dummy_hash = sha256hex(dummy)
    config = b"{}"
    config_hash = sha256hex(config)

    token = get_token(namespace, model)
    upload_blob(config, config_hash, token, namespace, model)
    upload_blob(dummy, dummy_hash, token, namespace, model)

    manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                   "digest": f"sha256:{config_hash}", "size": len(config)},
        "layers": [{"mediaType": "application/vnd.ollama.image.model",
                    "digest": f"sha256:{dummy_hash}", "size": len(dummy)}],
    }, separators=(",", ":")).encode()

    with httpx.Client(timeout=15) as c:
        r = c.put(f"{REGISTRY}/{namespace}/{model}/manifests/{tag}",
                  content=manifest,
                  headers={"Authorization": f"Bearer {token}",
                           "Content-Type": "application/vnd.docker.distribution.manifest.v2+json"})
        if r.status_code in (200, 201):
            print(f"    Manifest sobreescrito: {namespace}/{model}:{tag}")
        else:
            print(f"    [!] No se pudo sobreescribir manifest: HTTP {r.status_code}")


# ── Main ─────────────────────────────────────────────────────────────────────

metadata_file = Path("metadata.json")
if not metadata_file.exists():
    print("Sin metadata.json"); exit(0)

metadata = json.loads(metadata_file.read_text())
now = datetime.now(timezone.utc)
changed = False

for upload in metadata.get("uploads", []):
    if upload.get("expired"):
        continue
    expires_at = datetime.fromisoformat(upload["expires_at"])
    if now < expires_at:
        remaining = expires_at - now
        h = int(remaining.total_seconds() // 3600)
        m = int((remaining.total_seconds() % 3600) // 60)
        print(f"  Activo: {upload['model']} — expira en {h}h {m}m")
        continue

    print(f"\n[*] Expirando: {upload['model']}")

    # 1. Sobreescribir manifest en Ollama (borrado efectivo)
    if API_KEY:
        try:
            ns, name_tag = upload["model"].split("/", 1)
            name = name_tag.split(":")[0]
            tag  = name_tag.split(":")[1] if ":" in name_tag else "latest"
            overwrite_manifest(ns, name, tag)
        except Exception as e:
            print(f"    [!] Error sobreescribiendo manifest: {e}")
    else:
        print("    [!] OLLAMA_API_KEY no configurada — manifest no sobreescrito")

    # 2. Borrar archivo de llave
    kid = upload.get("kid")
    if kid:
        key_file = Path(f"keys/{kid}.json")
        if key_file.exists():
            key_file.unlink()
            print(f"    Llave borrada: {kid[:8]}...")

    upload["expired"] = True
    upload["expired_at"] = now.isoformat()
    changed = True
    print(f"    ✓ {upload['model']} eliminado")

if changed:
    metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    print("\nmetadata.json actualizado.")
else:
    print("Sin uploads expirados.")
