# Plan: Displaying Mnemonic Codes in the Admin Dashboard

This document outlines the rationale and implementation plan for integrating Mnemonic Codes into the SAM Admin Dashboard (specifically the Organizations and Institutions views) within the new Python `project_samuel` architecture.

## 1. Rationale and Visibility Gap

Displaying the Mnemonic Code in the Organizations and Institutions overview dashboard solves a major visibility gap for administrators:

*   **Visibility into Project Naming:** The Mnemonic Code is the literal 3-letter root (e.g., `UCB`) used to generate a project code (e.g., `NUCB0001`). An admin looking at an Institution (like "University of Colorado, Boulder") needs to know what project prefix will be assigned to its PIs.
*   **Surfacing "Soft Link" Disconnects (Crucial):** In the legacy Java codebase, `Institution` and `Organization` **do not have a hard foreign key** to `MnemonicCode`. The linkage is a "soft link" strategy pattern that matches the `Institution.name` (and sometimes `city`) to the `MnemonicCode.description`. Because it relies on exact string matching, it is highly susceptible to typos. If an admin edits an Institution's name, they might silently break the Mnemonic routing. Displaying the resolved Mnemonic Code on the dashboard immediately highlights broken or missing mappings.
*   **Clarifying Legacy Data:** The current `organization_card.html` template displays a generic "Code" (`{{ inst.code or '—' }}`). According to the legacy `mnemonicCode.sql` schema, the `institution.code` column was a legacy concept intended for removal that was superseded by the `mnemonic_code` table. Admins are likely confusing the old `inst.code` with the actual Mnemonic Code used for billing.

## 2. Python ORM Considerations

Because of the "soft link" design, the Python SQLAlchemy models (`Institution` and `Organization`) in `project_samuel/devel/src/sam/core/organizations.py` likely don't have a standard `relationship()` to a `MnemonicCode` model.

To bring this into the dashboard efficiently without causing N+1 query problems, consider these options:

### Option A: SQL Join / View (Recommended for Performance)
In the dashboard blueprint (`admin_dashboard/blueprint.py`), when querying institutions, perform an outer-join with the `MnemonicCode` table using the matching logic defined in the legacy system.

```python
from sqlalchemy import func

# Example logic for the query
institutions_with_mnemonics = db.session.query(
    Institution, 
    MnemonicCode.code.label('mnemonic_code')
).outerjoin(
    MnemonicCode,
    # The legacy Java logic matches Description to "Name, City" or just "Name"
    MnemonicCode.description == func.concat(Institution.name, ', ', Institution.city) | 
    MnemonicCode.description == Institution.name
).all()
```

### Option B: Hybrid Property on the ORM Model
Add a property to the `Institution` and `Organization` Python models to dynamically fetch the code. Note: This can cause N+1 query issues if lazy-loaded while rendering a large list in the dashboard.

```python
class Institution(db.Model):
    # ... existing fields ...

    @property
    def resolved_mnemonic(self):
        # Implementation of UserInstitutionStrategy in Python
        search_str = f"{self.name}, {self.city}" if self.city else self.name
        mnemonic = db.session.query(MnemonicCode).filter_by(description=search_str).first()
        if not mnemonic:
            # Fallback to just name
            mnemonic = db.session.query(MnemonicCode).filter_by(description=self.name).first()
        return mnemonic.code if mnemonic else None
```

## 3. Recommended UI Updates

Update the templates (e.g., `organization_card.html`) to show the Mnemonic.

### For the Institutions Tab:
Replace the legacy "Code" column with "Mnemonic Prefix" and add a visual warning if one is missing.

```html
<!-- Table Header -->
<th>Mnemonic Prefix</th>

<!-- Table Body -->
<td>
    {% if inst.resolved_mnemonic %}
        <span class="badge bg-primary">{{ inst.resolved_mnemonic }}</span>
    {% else %}
        <span class="badge bg-warning text-dark" title="No mnemonic found! Project creation may fail.">
            <i class="fas fa-exclamation-triangle"></i> Missing
        </span>
    {% endif %}
</td>
```

### For the Organizations Tab:
Organizations use a simpler `UserOrganizationStrategy` (which matches `Organization.name` to `MnemonicCode.description`). Adding a "Mnemonic Prefix" column to the `orgs-tree-table` gives admins a complete view of which branches of the organizational tree have dedicated project routing prefixes, using the same warning badge logic as above.

## Summary

Integrating the Mnemonic Code into the Organizations and Institutions dashboard views transitions the Mnemonic Code from a hidden backend routing rule into a visible administrative configuration. This gives the SSG/Admin team the ability to proactively spot project generation configuration issues (like mismatched names causing broken strategy links) before users encounter them during project creation.