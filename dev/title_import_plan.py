"""Local read-only planning; private output must be outside the Git checkout."""

import argparse
import hashlib
import json
from pathlib import Path

from core_contracts.extensions.tc_migration.title_import import MAX_BYTES, plan_title_import


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-sha256-prefix", required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    output = args.private_output.resolve()
    repo = Path(__file__).resolve().parents[1]
    if output == repo or repo in output.parents:
        raise SystemExit("Private report must remain outside the repository.")
    if args.input.stat().st_size != args.expected_size or args.expected_size > MAX_BYTES:
        raise SystemExit("Input size mismatch.")
    content = args.input.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    prefix = args.expected_sha256_prefix.lower()
    if len(prefix) < 16 or not digest.startswith(prefix):
        raise SystemExit("Input SHA-256 mismatch.")
    document = json.loads(
        content.decode("utf-8-sig"),
        object_pairs_hook=strict_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")),
    )
    plan = plan_title_import(document, digest)
    output.mkdir(parents=True, exist_ok=True)
    (output / "titelkatalog-probelauf.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Titelkatalog — privater Probelauf (nicht angewendet)",
        "",
        "SHA-256: " + digest,
        "",
        "## Summen",
        "",
        "```json",
        json.dumps(plan["summary"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Klassenbedeutung — von Benni auszufüllen",
        "",
        "| Quellklasse | Bedeutung | Zielklasse |",
        "|---|---|---|",
    ]
    lines += [f"| {n} | offen | offen |" for n in range(4)]
    lines += ["", "## Varianten mit abweichender Klasse", ""]
    for item in plan["variant_decisions"]:
        lines += [
            f"- Zeile {item['row']}: {item['key']!r}, Klasse {item['variant_class']}; Master {item['master']!r}, Klasse {item['master_class']}.",
            "  Entscheidung offen: **Variante übernimmt Masterklasse** oder **wird selbständiger Eintrag**.",
        ]
    lines += [
        "",
        "## Ausgeblendete Einträge",
        "",
        "Vorschlag: Ausblendung als nichtsemantischen Pflegehinweis erhalten; nichts still löschen oder umklassifizieren.",
        "",
    ]
    lines += [
        f"- Zeile {item['row']}: {item['key']!r}, Klasse {item['class']}; Entscheidung offen."
        for item in plan["hidden_decisions"]
    ]
    lines += ["", "## App-Einträge", ""]
    lines += [
        f"- Zeile {item['row']}: {item['key']!r} ({item['media_type']}); eigene Signalart app beibehalten."
        for item in plan["app_entries"]
    ]
    lines += [
        "",
        "Normalisierungskollisionen, alle Änderungen der Normalisierung, abgelehnte Zeilen mit Gründen und vollständige Kandidaten stehen im beigefügten privaten JSON.",
        "",
        plan["next_gate"],
    ]
    (output / "titelkatalog-probelauf.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Public-safe stdout is deliberately limited to counts/hash; never titles or raw rows.
    print(
        json.dumps(
            {"sha256": digest, "bytes": len(content), "summary": plan["summary"], "applied": False},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
