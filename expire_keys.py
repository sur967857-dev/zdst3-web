#!/usr/bin/env python3
"""Borra archivos de llave expirados y actualiza metadata.json."""
import json
from datetime import datetime, timezone
from pathlib import Path

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
    if now >= expires_at:
        kid = upload["kid"]
        key_file = Path(f"keys/{kid}.json")
        if key_file.exists():
            key_file.unlink()
            print(f"[✓] Llave borrada: {upload['model']} (kid={kid[:8]}...)")
        else:
            print(f"[!] Llave ya no existía: kid={kid[:8]}...")
        upload["expired"] = True
        upload["expired_at"] = now.isoformat()
        changed = True
        print(f"    Expirado: {upload['model']} — subido {upload['uploaded_at']}")

if changed:
    metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    print("metadata.json actualizado.")
else:
    print("Sin llaves expiradas.")
