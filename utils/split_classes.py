#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

def split_python_classes(source_path, output_dir=None):
    source_path = Path(source_path)
    if not source_path.exists():
        print(f"Error: {source_path} not found")
        sys.exit(1)

    if output_dir is None:
        output_dir = source_path.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    with open(source_path, 'r', encoding='utf-8') as f:
        code = f.read()

    # Match top-level class definitions only (not indented ones)
    # Captures: 'class ClassName(...):' or 'class ClassName:'
    class_pattern = re.compile(
        r'(?m)^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(.*?\))?:'
    )

    matches = list(class_pattern.finditer(code))
    if not matches:
        print("No classes found.")
        return

    for i, match in enumerate(matches):
        class_name = match.group(1)
        start = match.start()

        # end is start of next class or EOF
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        class_code = code[start:end].rstrip() + "\n"

        filename = output_dir / f"{class_name}.py"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(class_code)

        print(f"✅ Wrote {filename}")

    print("\nDone.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: split_classes.py big_module.py [output_dir]")
        sys.exit(1)
    source_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    split_python_classes(source_file, output_dir)
