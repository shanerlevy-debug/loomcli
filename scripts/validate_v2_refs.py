"""Quick validator — every $ref in schema/v2 resolves to a real file + $def.

Run from loomcli repo root:
    python scripts/validate_v2_refs.py
"""
import json
import os
import sys


def iter_refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from iter_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_refs(v)


def main() -> int:
    base = "schema/v2"
    schemas = {}
    for root, _, fns in os.walk(base):
        for fn in fns:
            if fn.endswith(".schema.json"):
                path = os.path.join(root, fn).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    schemas[path] = json.load(fh)

    errors = []
    for path, doc in schemas.items():
        local_defs = set(doc.get("$defs", {}).keys())
        for ref in iter_refs(doc):
            if ref.startswith("#/$defs/"):
                name = ref[len("#/$defs/"):]
                if name not in local_defs:
                    errors.append(f"{path}: unresolved internal ref {ref}")
                continue

            # File-relative ref
            file_part, _, frag = ref.partition("#")
            abs_target = os.path.normpath(
                os.path.join(os.path.dirname(path), file_part)
            ).replace(os.sep, "/")
            if abs_target not in schemas:
                errors.append(
                    f"{path}: unresolved file ref {ref} (target={abs_target})"
                )
                continue

            if frag:
                if frag.startswith("/$defs/"):
                    name = frag[len("/$defs/"):]
                    target_defs = schemas[abs_target].get("$defs", {})
                    if name not in target_defs:
                        errors.append(
                            f"{path}: cross-file ref {ref} — def {name!r} "
                            f"missing in {abs_target}"
                        )

    if errors:
        print("UNRESOLVED REFS:")
        for e in errors:
            print("  ", e)
        return 1

    print(f"All $refs resolved across {len(schemas)} schemas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
