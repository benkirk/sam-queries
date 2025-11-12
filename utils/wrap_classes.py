#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

import re

models = open("default_models.py", "w")
instantiate = open("instantiate_default_models.py", "w")
schema = open("schema.py", "w")

all_classes = set()

def camel_to_snake(name: str) -> str:
    # Insert underscore between lowercase and uppercase letters
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    # Handle cases like "HTTPRequest" -> "http_request"
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower()

def determine_class_names(source_path):
    source_path = Path(source_path)
    if not source_path.exists():
        print(f"Error: {source_path} not found")
        sys.exit(1)

    with open(source_path, 'r', encoding='utf-8') as f:
        code = f.read()
        print(code)

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
        all_classes.add(class_name)


def wrap_python_classes(class_name):
    class_view_name=f"{class_name}DefaultAdmin"
    class_view=f"# {class_name} Admin View\nclass {class_view_name}(ModelView):\n    pass\n"
    print(class_view, file=models)

    implementor=f"     admin.add_view({class_view_name}({class_name}, Session(), name='{class_name}', endpoint='default_views/{camel_to_snake(class_name)}', category='Everything'))"
    print(implementor, file=instantiate)

    class_schema=f"""# {class_name} marshmallow schema
class {class_name}Schema(SQLAlchemySchema):
    class Meta:
        model = {class_name}
        #load_instance = True  # Optional: deserialize to model instances
        include_relationships = True
        #include_fk = True
"""
    print(class_schema, file=schema)



if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: wrap_classes.py big_module.py [output_dir]")
        sys.exit(1)
    for source_file in sys.argv[1:]:
        determine_class_names(source_file)
    for class_name in sorted(all_classes):
        wrap_python_classes(class_name)
