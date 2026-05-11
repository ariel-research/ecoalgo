# Category Renaming Guide

This guide covers every file that must be edited when you rename or remove a survey category.

---

## Concepts

Each category has two parts:
- **Key** — the internal string stored in the database (`fair_division`, `approval_voting`, …). Changing the key requires a DB migration.
- **Label** — the human-readable display name shown in the UI. Changing only the label is a one-file change.

---

## Case 1 — Change only the display label (easiest)

**File: `algorithms.py`**

```python
CATEGORIES = {
    'fair_division': 'Fair Division',   # ← change this string
    ...
}
```

That's it. Templates read the label from Python via `CATEGORIES.items()`, so they update automatically.

**Also update** the description for the homepage card in **`app.py`**:

```python
CATEGORY_INFO = {
    'fair_division': {
        'icon':        '⚖️',
        'description': 'New description here.',
    },
    ...
}
```

---

## Case 2 — Rename the internal key

Renaming the key (e.g. `fair_division` → `fair_allocation`) touches more files because the key is stored in the database and referenced in templates.

### 1. `algorithms.py`

```python
# CATEGORIES dict
CATEGORIES = {
    'fair_allocation': 'Fair Allocation',   # new key
    ...
}

# Every algorithm that belonged to the old category
'round_robin': {
    'category': 'fair_allocation',          # update here too
    ...
}
```

### 2. `app.py`

**`CATEGORY_SETTINGS`** — move the entry to the new key:
```python
CATEGORY_SETTINGS = {
    'fair_allocation': {                    # renamed key
        'use_weights':           False,
        'require_user_capacity': False,
        'use_item_capacity':     False,
    },
    ...
}
```

**`CATEGORY_INFO`** — same:
```python
CATEGORY_INFO = {
    'fair_allocation': { ... },             # renamed key
    ...
}
```

### 3. `templates/survey/edit.html`

Search for any template conditionals that hard-code the old key:

```jinja
{# ranking-mode selector visibility #}
{% if survey.category in ('approval_voting', 'participatory_budgeting') %}

{# budget field visibility #}
{% if survey.category == 'participatory_budgeting' %}

{# item cost column #}
{% if survey.use_weights or survey.category == 'participatory_budgeting' %}
```

Also update the JavaScript category-change handler at the bottom of the file:

```javascript
function editOnCategoryChange(cat) {
    const isPB = cat === 'participatory_budgeting';   // update string if key changed
    ...
}
```

### 4. `templates/survey/create.html`

Update the `CATEGORY_HINTS` object and the `onCategoryChange` function:

```javascript
const CATEGORY_HINTS = {
    fair_allocation: 'New hint text.',      // renamed key
    ...
};

function onCategoryChange(cat) {
    const isPB = cat === 'participatory_budgeting';   // update if key changed
    ...
}
```

### 5. Database migration

The `Survey.category` column stores the key. Run this once on the live database:

```sql
UPDATE survey SET category = 'fair_allocation' WHERE category = 'fair_division';
UPDATE allocation_result SET category = 'fair_allocation' WHERE category = 'fair_division';
```

Or add it to `run_migrations()` in `app.py`:

```python
conn.execute(db.text(
    "UPDATE survey SET category='fair_allocation' WHERE category='fair_division'"
))
conn.execute(db.text(
    "UPDATE allocation_result SET category='fair_allocation' WHERE category='fair_division'"
))
conn.commit()
```

---

## Case 3 — Remove a category

### 1. `algorithms.py` — remove from `CATEGORIES`

```python
CATEGORIES = {
    # 'budget_allocation': 'Budget Allocation',   ← delete this line
    ...
}
```

If the category had algorithms registered, remove them from `ALGORITHMS` too (or re-assign them to another category).

### 2. `app.py` — remove from `CATEGORY_SETTINGS` and `CATEGORY_INFO`

```python
CATEGORY_SETTINGS = {
    # 'budget_allocation': { ... },   ← delete
    ...
}

CATEGORY_INFO = {
    # 'budget_allocation': { ... },   ← delete
    ...
}
```

### 3. `templates/survey/edit.html` — remove key from conditionals

```jinja
{# Before #}
{% if survey.category in ('budget_allocation', 'approval_voting', 'participatory_budgeting') %}

{# After #}
{% if survey.category in ('approval_voting', 'participatory_budgeting') %}
```

Same for the JS handler at the bottom:

```javascript
// Remove the isBudget line and any logic that depends on it
function editOnCategoryChange(cat) {
    const isApproval = cat === 'approval_voting';
    const isPB       = cat === 'participatory_budgeting';
    const hideMode   = isApproval || isPB;
    ...
}
```

### 4. `templates/survey/create.html` — remove from `CATEGORY_HINTS` and `onCategoryChange`

```javascript
const CATEGORY_HINTS = {
    // budget_allocation: '...',   ← delete
    ...
};

function onCategoryChange(cat) {
    // const isBudget = cat === 'budget_allocation';   ← delete
    const isPB = cat === 'participatory_budgeting';
    ...
}
```

---

## Summary table

| What changes | Display label only | Key rename | Remove category |
|---|:---:|:---:|:---:|
| `algorithms.py` — CATEGORIES | ✅ | ✅ | ✅ |
| `algorithms.py` — algorithm entries | — | ✅ | ✅ (if any) |
| `app.py` — CATEGORY_SETTINGS | — | ✅ | ✅ |
| `app.py` — CATEGORY_INFO | ✅ | ✅ | ✅ |
| `templates/survey/edit.html` conditionals | — | ✅ | ✅ |
| `templates/survey/create.html` JS | — | ✅ | ✅ |
| Database `survey.category` column | — | ✅ (SQL) | — |
| Database `allocation_result.category` | — | ✅ (SQL) | — |
