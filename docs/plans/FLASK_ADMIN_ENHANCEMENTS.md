# Flask-Admin Model View Enhancements

## Overview

This document outlines planned enhancements to the Flask-Admin interface for SAM models, leveraging the common mixins defined in `src/sam/base.py` to provide consistent, user-friendly defaults across all 80+ admin views.

**Status**: Planning / Not Implemented
**Target**: `src/webapp/admin/default_model_views.py`
**Created**: 2025-12-09

---

## Goals

1. **Auto-hide soft-deleted records** - Prevent accidental viewing/editing of deleted data
2. **Consistent filtering** - Add filters based on model mixins (active, deleted, timestamps)
3. **Smart defaults** - Auto-populate searchable fields, exclude internal columns from forms
4. **Better UX** - Default sorting by creation time, status indicators visible

---

## Mixin-Based Enhancements

### SoftDeleteMixin (`deleted`, `deletion_time`)
**Models affected**: ~30+ models with soft delete capability

#### Changes
- **Auto-hide deleted records** in list views (override `get_query()` and `get_count_query()`)
- **Add `deleted` filter** to allow admins to view deleted records when needed
- **Exclude `deletion_time` from forms** (read-only, set by system)
- **Visual indicator** for deleted status in column formatters (if shown)

#### Implementation Priority
🔴 **Critical** - Most important enhancement to prevent data confusion

```python
def get_query(self):
    """Override to exclude soft-deleted records by default."""
    query = super().get_query()
    if self.auto_hide_deleted and hasattr(self.model, 'deleted'):
        query = query.filter_by(deleted=False)
    return query

def get_count_query(self):
    """Override count query to match filtered query."""
    query = super().get_count_query()
    if self.auto_hide_deleted and hasattr(self.model, 'deleted'):
        query = query.filter_by(deleted=False)
    return query
```

---

### ActiveFlagMixin (`active`)
**Models affected**: ~40+ models with active status

#### Changes
- **Add `active` filter** as first/prominent filter option
- **Show `active` in column_list** (early in list, after ID/primary identifier)
- **Optional**: Default to hiding inactive (via flag `auto_hide_inactive = False`)
- **Column formatter**: Visual indicator (✓/✗ or color coding)

#### Implementation Priority
🟡 **High** - Frequently used filter, should be easily accessible

#### Note
Unlike `deleted`, we **do NOT recommend** hiding inactive by default in SAM context:
- Inactive projects/users need periodic review
- Historical context is important
- Deactivated ≠ removed from system

---

### TimestampMixin (`creation_time`, `modified_time`)
**Models affected**: ~80+ models (nearly all)

#### Changes
- **Exclude from forms** - These are system-managed, not user-editable
- **Add filters**: "Created After", "Modified After" (date range filters)
- **Default sort**: `column_default_sort = ('creation_time', True)` (descending)
- **Show in column_list**: At the end of the list (low priority for quick scanning)
- **Show in details view**: Always include for audit trail

#### Implementation Priority
🟢 **Medium** - Quality of life improvement

```python
# Auto-exclude from all forms
form_excluded_columns = ['creation_time', 'modified_time', 'deletion_time']

# Auto-sort by newest first (if no other sort specified)
if not hasattr(self, 'column_default_sort') and hasattr(self.model, 'creation_time'):
    column_default_sort = ('creation_time', True)
```

---

### DateRangeMixin (`start_date`, `end_date`)
**Models affected**: ~15 models (allocations, user-resource relationships, etc.)

#### Changes
- **Add filters**: "Start Date After/Before", "End Date After/Before"
- **Add "Currently Active" filter**: Uses the `is_currently_active` hybrid property
- **Default sort**: `column_default_sort = ('start_date', True)` (descending)
- **Column formatter**: Show date range as "2024-01-01 → 2025-12-31" or "2024-01-01 → ongoing"

#### Implementation Priority
🟢 **Medium** - Useful for allocation/resource management views

```python
# Example custom filter for "Currently Active"
class IsCurrentlyActiveFilter(BaseSQLAFilter):
    def apply(self, query, value):
        if value == '1':
            return query.filter(self.model.is_currently_active)
        return query

# Add to column_filters
column_filters = [
    'start_date', 'end_date',
    IsCurrentlyActiveFilter(column=None, name='Currently Active')
]
```

---

## Auto-Detection Features

### 1. Auto-Searchable String Columns
**Goal**: Make all `VARCHAR`/`String` columns searchable by default

```python
def scaffold_searchable_columns(self):
    """Auto-populate searchable list with string columns."""
    if hasattr(self, '_manual_searchable') and self._manual_searchable:
        return self.column_searchable_list  # Respect manual override

    searchable = []
    excluded_patterns = ['password', 'hash', 'token', 'secret', 'key']

    for column_name, column in self.model.__mapper__.columns.items():
        # Add string columns, exclude sensitive fields
        if isinstance(column.type, String):
            if not any(pattern in column_name.lower() for pattern in excluded_patterns):
                searchable.append(column_name)

    return searchable
```

**Priority**: 🟢 **Medium**
**Note**: Individual views can override by setting `_manual_searchable = True`

---

### 2. Auto-Exclude System Columns from Forms

```python
# Class-level defaults in SAMModelView
form_excluded_columns = [
    'creation_time', 'modified_time', 'deletion_time',  # TimestampMixin
    'deleted',  # SoftDeleteMixin - use soft delete action instead
]

# Auto-append mixin columns in __init__ or scaffold_form
def scaffold_form(self):
    form_class = super().scaffold_form()

    # Auto-exclude system-managed fields
    auto_exclude = set()
    if hasattr(self.model, 'creation_time'):
        auto_exclude.update(['creation_time', 'modified_time'])
    if hasattr(self.model, 'deleted'):
        auto_exclude.update(['deleted', 'deletion_time'])

    # Merge with user-defined exclusions
    self.form_excluded_columns = list(set(self.form_excluded_columns or []) | auto_exclude)

    return form_class
```

**Priority**: 🔴 **Critical** - Prevents user confusion and errors

---

### 3. Smart Column List Ordering

**Recommended pattern**:
1. Primary ID (`user_id`, `project_id`, `account_id`)
2. Primary identifier (`username`, `projcode`, `resource_name`)
3. Status flags (`active`, `deleted`) - **NEW: auto-prepend these**
4. Key attributes (domain-specific)
5. Timestamps (`creation_time`, `modified_time`) - always last

```python
def scaffold_list_columns(self):
    """Auto-order columns with status flags early, timestamps late."""
    columns = super().scaffold_list_columns()

    # Identify column types
    status_cols = []
    timestamp_cols = []
    other_cols = []

    for col_name in columns:
        if col_name in ['active', 'deleted', 'locked']:
            status_cols.append(col_name)
        elif col_name in ['creation_time', 'modified_time', 'deletion_time']:
            timestamp_cols.append(col_name)
        else:
            other_cols.append(col_name)

    # Re-order: other columns first, then status, then timestamps
    # (ID columns naturally come first in other_cols)
    return other_cols + status_cols + timestamp_cols
```

**Priority**: 🟢 **Low** - Nice to have, improves scanning

---

## Implementation Strategy

### Phase 1: Core Infrastructure (Week 1)
**Goal**: Add opt-in flags and base functionality to `SAMModelView`

```python
class SAMModelView(ModelView):
    # Feature flags (opt-in initially, default to True in Phase 2)
    auto_hide_deleted = True          # 🔴 Critical
    auto_hide_inactive = False        # Explicitly opt-in only
    auto_exclude_system_columns = True  # 🔴 Critical
    auto_filter_mixins = True         # 🟡 High
    auto_searchable_strings = False   # 🟢 Medium - opt-in initially

    # Standard exclusions
    form_excluded_columns = ['creation_time', 'modified_time', 'deletion_time']

    # Override get_query() for soft delete filtering
    # Override scaffold_form() for auto-exclusions
    # Override scaffold_filters() for mixin-based filters
```

**Testing**:
- Test on 3-5 representative models (User, Project, Account, Allocation, Resource)
- Verify deleted records are hidden
- Verify filters appear correctly
- Verify forms exclude system columns

**Deliverables**:
- [ ] Updated `SAMModelView` with feature flags
- [ ] Unit tests for query filtering
- [ ] Documentation in docstrings

---

### Phase 2: Auto-Detection (Week 2)
**Goal**: Add intelligent defaults based on model introspection

**Tasks**:
- [ ] Implement `scaffold_searchable_columns()` for string auto-search
- [ ] Implement `scaffold_list_columns()` for smart ordering
- [ ] Implement default sort detection (creation_time → DESC)
- [ ] Add "Currently Active" filter for DateRangeMixin models

**Testing**:
- Test auto-search on models with many string columns
- Verify sensitive columns (password, token) are excluded
- Verify column ordering makes sense

**Deliverables**:
- [ ] Auto-detection methods implemented
- [ ] Integration tests on 10+ models
- [ ] Updated CLAUDE.md with new patterns

---

### Phase 3: Custom View Migration (Week 3)
**Goal**: Review and simplify custom views using new defaults

**Tasks**:
- [ ] Review `src/webapp/admin/custom_model_views.py`
- [ ] Remove redundant configurations now handled by defaults
- [ ] Add opt-out flags where custom behavior is desired
- [ ] Update column formatters for status indicators

**Example - UserAdmin Before**:
```python
class UserAdmin(SAMModelView):
    column_list = ('user_id', 'username', 'full_name', 'primary_email', 'active', 'locked')
    column_searchable_list = ('username', 'first_name', 'last_name')
    column_filters = ('active', 'locked', 'charging_exempt')
    form_excluded_columns = ('creation_time', 'modified_time', 'led_projects', ...)
```

**Example - UserAdmin After**:
```python
class UserAdmin(SAMModelView):
    # Auto-searchable: username, first_name, last_name (all strings)
    # Auto-filters: active (from ActiveFlagMixin)
    # Auto-excluded: creation_time, modified_time

    # Only specify customizations:
    column_list = ('user_id', 'username', 'full_name', 'primary_email', 'active', 'locked')
    column_filters = SAMModelView.column_filters + ['locked', 'charging_exempt']
    form_excluded_columns = SAMModelView.form_excluded_columns + ['led_projects', 'admin_projects', ...]

    column_formatters = {
        'full_name': lambda v, c, m, p: m.full_name,
        'primary_email': lambda v, c, m, p: m.primary_email or 'N/A'
    }
```

**Deliverables**:
- [ ] Refactored custom views (cleaner, less redundant)
- [ ] Regression testing (ensure no functionality lost)
- [ ] Updated custom view documentation

---

### Phase 4: Default View Generation (Week 4)
**Goal**: Auto-generate better defaults for the 60+ default views

Currently `default_model_views.py` has 60+ empty classes:
```python
class AcademicStatusDefaultAdmin(SAMModelView):
    pass
```

**Enhancement**: Generate sensible defaults based on model introspection

```python
# NEW: utils/generate_default_views.py
def generate_default_admin(model_class):
    """Generate sensible defaults for a SAM model admin view."""
    attrs = {}

    # Auto-detect primary identifier column
    # (username, projcode, resource_name, etc.)
    primary_id = detect_primary_identifier(model_class)

    # Build column_list
    id_col = f"{model_class.__tablename__}_id"
    attrs['column_list'] = [id_col, primary_id]

    # Add status columns if present
    if hasattr(model_class, 'active'):
        attrs['column_list'].append('active')
    if hasattr(model_class, 'deleted'):
        attrs['column_list'].append('deleted')

    # Add 3-5 most important columns (heuristic-based)
    # Add creation_time if present

    return type(f"{model_class.__name__}DefaultAdmin", (SAMModelView,), attrs)
```

**Deliverables**:
- [ ] Script to analyze and generate better defaults
- [ ] Regenerate `default_model_views.py` with enhanced defaults
- [ ] Validation that all 80+ views still work

---

## Configuration Options

### Per-View Opt-Out Flags

```python
class MySpecialAdmin(SAMModelView):
    # Disable auto-features if needed
    auto_hide_deleted = False       # Show deleted records by default
    auto_filter_mixins = False      # Don't add automatic filters
    auto_searchable_strings = False # Use manual searchable list only
    _manual_searchable = True       # Flag to prevent auto-search override

    # Manual configuration takes precedence
    column_searchable_list = ['specific_field_only']
```

---

## Testing Plan

### Unit Tests (`tests/unit/test_admin_defaults.py`)
- [ ] Test `get_query()` filters deleted records
- [ ] Test `get_count_query()` matches filtered count
- [ ] Test `scaffold_form()` excludes system columns
- [ ] Test `scaffold_filters()` adds mixin-based filters
- [ ] Test `scaffold_searchable_columns()` finds string columns
- [ ] Test sensitive column exclusion (password, token, hash)

### Integration Tests (`tests/integration/test_admin_views.py`)
- [ ] Test 10+ representative models render correctly
- [ ] Test create/edit forms work without system columns
- [ ] Test filters work (active, deleted, date ranges)
- [ ] Test search works on auto-detected columns
- [ ] Test soft-deleted records are hidden
- [ ] Test "Show Deleted" filter reveals deleted records

### Manual Testing Checklist
- [ ] Create a new user (form should not show creation_time)
- [ ] Filter projects by active status
- [ ] Search users by first/last name
- [ ] Verify deleted allocations don't appear in list
- [ ] Toggle "Show Deleted" filter to reveal deleted records
- [ ] Sort projects by creation_time (newest first)
- [ ] Filter allocations by "Currently Active"

---

## Success Criteria

### Quantitative
- ✅ 100% of models with `SoftDeleteMixin` hide deleted records by default
- ✅ 100% of models with `TimestampMixin` exclude system columns from forms
- ✅ 80%+ reduction in boilerplate configuration in custom views
- ✅ All existing tests continue to pass
- ✅ Zero regressions in admin functionality

### Qualitative
- ✅ Admins can quickly filter by active/deleted status
- ✅ No accidental editing of system-managed fields
- ✅ Consistent UX across all 80+ admin views
- ✅ Easy to opt-out of auto-features when needed
- ✅ Clear documentation for future view creation

---

## Migration Path

### Backward Compatibility
- All enhancements are **additive** - no breaking changes
- Feature flags default to `True` after Phase 1 validation
- Existing custom views continue to work unchanged
- New defaults only affect the 60+ empty `pass` views initially

### Rollback Plan
- Feature flags allow per-view disabling
- Can globally disable via `SAMModelView` base class flags
- Separate PR for each phase (can revert individual phases)

---

## Future Enhancements (Post-MVP)

### 1. Visual Status Indicators
```python
column_formatters = {
    'active': lambda v, c, m, p: '✓ Active' if m.active else '✗ Inactive',
    'deleted': lambda v, c, m, p: '🗑️ Deleted' if m.deleted else '',
}
```

### 2. Bulk Actions
- Bulk soft-delete (set `deleted=True`)
- Bulk activate/deactivate
- Bulk export

### 3. Quick Filters
- "Created in last 7 days"
- "Created in last 30 days"
- "Modified recently"
- "Expiring soon" (for DateRangeMixin)

### 4. Relationship Previews
- Show count of related objects in list view
- E.g., User list shows "5 projects", "12 accounts"

### 5. Audit Trail View
- Dedicated view for `TimestampMixin` fields
- "Who created when, who modified when"

---

## Resources

### Related Files
- `src/sam/base.py` - Mixin definitions
- `src/webapp/admin/default_model_views.py` - 60+ default views to enhance
- `src/webapp/admin/custom_model_views.py` - 6 custom views to simplify
- `tests/unit/test_basic_read.py` - Model relationship tests
- `tests/integration/test_schema_validation.py` - Schema validation

### Flask-Admin Documentation
- [Customizing Model Views](https://flask-admin.readthedocs.io/en/latest/api/mod_contrib_sqla/)
- [Filters](https://flask-admin.readthedocs.io/en/latest/api/mod_model/#module-flask_admin.model.filters)
- [Column Formatters](https://flask-admin.readthedocs.io/en/latest/advanced/#formatters)

### SQLAlchemy Patterns
- [Hybrid Attributes](https://docs.sqlalchemy.org/en/20/orm/extensions/hybrid.html) - Used by `DateRangeMixin.is_currently_active`
- [Mixins](https://docs.sqlalchemy.org/en/20/orm/declarative_mixins.html)

---

## Questions for Review

1. **Auto-hide inactive**: Should `ActiveFlagMixin` hide inactive records by default? (Currently: No)
2. **Search sensitivity**: Are there other column patterns to exclude from auto-search besides password/token/hash?
3. **Performance**: Do auto-filters impact query performance on large tables?
4. **Column ordering**: Should we enforce strict ordering or just suggest it?
5. **View regeneration**: Should Phase 4 regenerate all default views or migrate incrementally?

---

## Implementation Owner

**TBD** - Assign to developer or team

## Timeline

- **Phase 1**: 1 week (Core infrastructure)
- **Phase 2**: 1 week (Auto-detection)
- **Phase 3**: 1 week (Custom view migration)
- **Phase 4**: 1 week (Default view generation)

**Total**: ~4 weeks for full implementation

---

*Document Status*: Draft
*Last Updated*: 2025-12-09
*Related Issue*: TBD
