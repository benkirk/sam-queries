from sqlalchemy import inspect
from sam_models import *

def validate_relationships(Base):
    """Validate all relationships have proper back_populates."""
    issues = []

    for mapper in Base.registry.mappers:
        cls = mapper.class_
        for rel in mapper.relationships:
            if rel.back_populates is None and rel.backref is None:
                # Check if it's intentionally viewonly
                if not rel.viewonly:
                    issues.append(
                        f"{cls.__name__}.{rel.key} -> {rel.mapper.class_.__name__} "
                        f"missing back_populates"
                    )
            elif rel.back_populates:
                # Verify the back_populates exists on target
                target_mapper = rel.mapper
                if rel.back_populates not in [r.key for r in target_mapper.relationships]:
                    issues.append(
                        f"{cls.__name__}.{rel.key} back_populates='{rel.back_populates}' "
                        f"but {target_mapper.class_.__name__}.{rel.back_populates} doesn't exist"
                    )

    return issues

# Run validation
issues = validate_relationships(Base)
if issues:
    print("❌ Relationship Issues Found:")
    for issue in issues:
        print(f"  - {issue}")
else:
    print("✅ All relationships properly configured!")
