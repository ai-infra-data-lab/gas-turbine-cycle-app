"""Reproducible subset extraction from the pinned NASA CEA thermo.inp (offline)."""
import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE_COMMIT = "985fd33368b2334c4162a586c499e0ab41d12bd3"
SOURCE_SHA256 = "fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363"
SPECIES = ("N2", "O2", "H2O", "CO2", "Ar")


def extract(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source SHA-256 does not match the pinned NASA dataset")
    # NASA source includes CRCRLF; splitlines then remove empty logical lines.
    lines = [x for x in raw.decode("ascii").splitlines() if x.strip()]
    output = {}
    for name in SPECIES:
        i = next(i for i, line in enumerate(lines) if line[:18].strip() == name and not line.startswith(" "))
        header = lines[i + 1]
        ranges = []
        for j in range(int(header[:2])):
            line = lines[i + 2 + j * 3]
            low, high = float(line[:11]), float(line[11:22])
            a = lines[i + 3 + j * 3]
            b = lines[i + 4 + j * 3]
            # The 16-character blank slot between a7 and b1 is part of NASA's format.
            coefficients = [float(a[k:k + 16].replace("D", "E")) for k in range(0, 80, 16)]
            coefficients += [float(b[k:k + 16].replace("D", "E")) for k in (0, 16, 48, 64)]
            if high <= 6000:
                ranges.append({"t_min_k": low, "t_max_k": high, "coefficients": coefficients})
        output[name] = {"reference": lines[i][18:].strip(), "molecular_weight_kg_kmol": float(header[52:65]), "ranges": ranges}
    return {"dataset_id": "nasa-cea-985fd33368b2-nasa9-5species", "format": "NASA9", "source_repository": "nasa/cea",
            "source_commit": SOURCE_COMMIT, "source_url": f"https://raw.githubusercontent.com/nasa/cea/{SOURCE_COMMIT}/data/thermo.inp",
            "source_file_sha256": hashlib.sha256(raw).hexdigest(), "retrieved_date": "2026-10-27",
            "universal_gas_constant_j_kmol_k": 8314.51, "temperature_range_k": [200, 6000],
            "modifications": "Five gas species extracted; intervals above 6000 K omitted; numeric coefficients unchanged; reformatted as JSON.",
            "species": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = extract(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(data["dataset_id"])
