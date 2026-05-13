# EcoAlgo — Economic Algorithms Platform

A web platform for running economic algorithms on real survey data. Every registered user is a moderator, who can create a survey, invite participants to submit their preferences, and then run an algorithm to compute an allocation or outcome. Participants see their results without needing to know anything about the algorithm.

---

## Tech Stack

| Layer | Library / Tool |
|-------|---------------|
| Web framework | Flask + Flask-Security + Flask-WTF |
| Database | SQLite via SQLAlchemy |
| Frontend | Bootstrap 5 + Rubik (Google Fonts) |
| Fair division | [fairpyx](https://github.com/erelsgl/fairpyx) |
| Approval voting | [abcvoting](https://github.com/martinlackner/abcvoting) |
| Participatory budgeting | [pabutools](https://github.com/COMSOC-Community/pabutools) |

---

## Algorithm Categories

Categories determine what data the survey collects from participants and how that data is passed to the algorithm. Each category forces a specific **ranking mode** and may enable item/agent fields.

### 1. Fair Division (`fair_division`)
Participants rank or rate items. Each item is assigned to exactly one agent.

**Ranking modes:** ordinal (drag-and-drop), rating (numeric score)
**Algorithm input:** `fairpyx.Instance(valuations, item_capacities)`

| Algorithm | Key |
|-----------|-----|
| Round Robin | `round_robin` |
| Bidirectional Round Robin | `bidirectional_round_robin` |
| Serial Dictatorship | `serial_dictatorship` |
| Utilitarian Matching | `utilitarian_matching` |
| Almost Egalitarian | `almost_egalitarian_allocation` |
| Almost Egalitarian (No Donation) | `almost_egalitarian_without_donation` |
| Almost Egalitarian (With Donation) | `almost_egalitarian_with_donation` |
| Fractional Egalitarian | `fractional_egalitarian_allocation` |
| Fractional Egalitarian-Utilitarian | `fractional_egalitarian_utilitarian_allocation` |
| Maximally Proportional | `maximally_proportional_allocation` |
| Gale-Shapley | `gale_shapley` |
| Ordinal/Cardinal (OC) | `OC_function` |
| Top Trading Cycles (TTC) | `TTC_function` |
| TTC Optimized | `TTC_O_function` |
| Second Price (SP) | `SP_function` |
| Second Price Optimized (SP-O) | `SP_O_function` |

---

### 2. Capacitated & Weighted Allocation (`capacitated_allocation`)
Like fair division, but each **agent** has a weight and a capacity (how many items they can receive), and each **item** has a capacity (how many agents can receive it).

**Ranking mode:** ordinal
**Extra fields:** `item.weight`, `item.capacity`, `participant.user_weight`, `participant.user_capacity`
**Algorithm input:** `fairpyx.Instance(valuations, item_capacities, agent_capacities, agent_target_weights)`

| Algorithm | Key |
|-----------|-----|
| Iterated Maximum Matching | `iterated_maximum_matching` |
| Iterated Maximum Matching (Adjusted) | `iterated_maximum_matching_adjusted` |
| Iterated Maximum Matching (Unadjusted) | `iterated_maximum_matching_unadjusted` |

---

### 3. Approval Voting (`approval_voting`)
Participants tick the items they approve. The algorithm selects a **winning committee** of a given size.

**Ranking mode:** approval (forced, stored as `ItemRanking.points = 1/0`)
**Extra run-time parameter:** `committeesize` (entered by moderator when running)
**Algorithm input:** `abcvoting.preferences.Profile`

| Algorithm | Key | Notes |
|-----------|-----|-------|
| Proportional Approval Voting (PAV) | `pav` | |
| Sequential PAV | `seqpav` | |
| Sequential Chamberlin-Courant | `seqcc` | |
| Sequential Phragmén | `seqphragmen` | |
| Method of Equal Shares (Rule X) | `equal_shares` | Extra `completion` parameter |

---

### 4. Participatory Budgeting (`participatory_budgeting`)
Participants tick projects they support. Each project has a **cost** (`item.weight`) and the survey has a **global budget** (`survey.total_points`). The algorithm funds a subset of projects within budget.

**Ranking mode:** approval (forced)
**Extra fields:** `item.weight` = project cost; `survey.total_points` = budget
**Algorithm input:** `pabutools.election.Instance` + `pabutools.election.ApprovalProfile`

| Algorithm | Key | sat_class needed |
|-----------|-----|-----------------|
| Sequential Phragmén | `pb_seq_phragmen` | No |
| Method of Equal Shares | `pb_equal_shares` | Yes (`Cardinality_Sat`) |
| Max Additive Utilitarian Welfare | `pb_max_welfare` | Yes |
| Greedy Utilitarian Welfare | `pb_greedy_welfare` | Yes |

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd ecoalgo

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate.ps1       # Windows Powershell

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python app.py
```

The app starts at `http://localhost:5032`.
Default admin credentials: `admin@example.com` / `adminpassword` (change immediately in production).

---

## Key Files

```
app.py          — Flask routes, category settings, algorithm runner
algorithms.py   — Algorithm registry, builder functions, CATEGORIES dict
models.py       — SQLAlchemy models (Survey, SurveyItem, SurveyParticipant, ItemRanking, ...)
config.py       — Flask / SQLAlchemy configuration
static/
  style.css     — Custom CSS (warm blue palette, Rubik font)
templates/
  base.html     — Navbar, footer, font imports
  home.html     — Homepage with category cards
  survey/       — Create, edit, rank, results templates
  admin/        — Admin dashboard and settings
```

---

## Data Flow Summary

```
Survey (category, ranking_mode, total_points)
  └─ SurveyItem (name, weight/cost, capacity)
  └─ SurveyParticipant (user_weight, user_capacity)
       └─ ItemRanking (rank | points | rating)

    ↓  builder function (in algorithms.py)

Algorithm input object
  fairpyx  → Instance(valuations, item_capacities, ...)
  abcvoting → Profile(num_cand, cand_names) + voters
  pabutools → Instance(projects, budget_limit) + ApprovalProfile

    ↓  runner branch (in app.py → survey_run_algorithm)

Result → AllocationResult.result_json
  fairpyx      → { "allocation": {agent: [items]}, ... }
  abcvoting    → { "committees": [[items], ...], "committeesize": N, ... }
  pabutools    → { "funded_projects": [items], "total_cost": N, "budget": N, ... }
```

---

## Adding More Algorithms — Prompt for Claude

If you are starting a **new conversation with Claude** and want to add algorithms or libraries, paste the following prompt (filling in the blanks):

---

> ### Context
>
> I have a Flask web app called **EcoAlgo** (`app.py`, `algorithms.py`, `models.py`).
> The app lets a moderator create a survey, collect participant preferences, and run economic algorithms on the data.
>
> ### Architecture
>
> **`algorithms.py`** contains:
> - `CATEGORIES` dict — `{ key: label }` — one entry per survey type.
> - `CATEGORY_SETTINGS` (in `app.py`) — maps each category key to survey flags:
>   `use_weights`, `require_user_capacity`, `use_item_capacity`, `ranking_mode`.
>   This controls what fields the survey UI shows and what data participants fill in.
> - Builder functions — convert a `Survey` ORM object into the input object the algorithm expects.
>   Current builders: `build_standard_instance`, `build_capacitated_instance` (both → `fairpyx.Instance`),
>   `build_approval_profile` (→ `abcvoting Profile`), `build_pabutools_instance_and_profile` (→ `(Instance, ApprovalProfile)`).
> - `ALGORITHMS` dict — one entry per algorithm:
>   ```python
>   'key': {
>       'category':     'category_key',
>       'display_name': 'Human Name',
>       'group':        'Group label in dropdown',
>       'description':  'Shown in UI',
>       # for fairpyx:
>       'module':   'fairpyx.algorithms.some_module',
>       'function': 'function_name',
>       'builder':  build_standard_instance,  # or build_capacitated_instance
>       # for abcvoting:
>       'runner':       'abcvoting',
>       'rule_id':      'rule-id-string',
>       'builder':      build_approval_profile,
>       'extra_params': ['completion'],  # optional — form fields passed as kwargs
>       # for pabutools:
>       'runner':          'pabutools',
>       'module':          'pabutools.rules',
>       'function':        'function_name',
>       'builder':         build_pabutools_instance_and_profile,
>       'needs_sat_class': True,  # injects Cardinality_Sat automatically
>   }
>   ```
>
> **`app.py`** contains:
> - `CATEGORY_SETTINGS` — ranking mode and boolean flags per category.
> - `survey_run_algorithm` — branches on `entry.get('runner')`:
>   `'pabutools'` → calls builder, imports function, injects `Cardinality_Sat` if `needs_sat_class`,
>   stores result as `{ funded_projects, total_cost, budget, ... }`.
>   `'abcvoting'` → calls `abcrules.compute(rule_id, profile, committeesize, **extra_kwargs)`,
>   stores result as `{ committees, committeesize, ... }`.
>   *(default/no runner)* → uses `fairpyx.divide(algo_func, instance)`,
>   stores result as `{ allocation: {agent: [items]}, ... }`.
>
> **Ranking modes** (stored in `Survey.ranking_mode`):
> - `'ordinal'` — drag-and-drop ranking; stored in `ItemRanking.rank`
> - `'rating'`  — numeric score; stored in `ItemRanking.rating`
> - `'budget'`  — distribute `total_points`; stored in `ItemRanking.points`
> - `'approval'`— tick approved items; stored in `ItemRanking.points` (1=approved, 0=not)
>
> **Results display** (`templates/survey/results.html`) branches on:
> - `result.data.get('funded_projects')` → participatory budgeting display
> - `result.data.get('committees')` → approval voting committee display
> - `result.data.get('allocation')` → per-agent allocation table
>
> ### What I want to add
>
> *(Describe the library and algorithms here. Include:)*
> 1. Library name and pip install command.
> 2. The algorithm function signatures (what arguments they take).
> 3. What input object they expect — and whether it is the same shape as an existing category or needs a new one.
> 4. What their output looks like.
>
> ### Grouping rule
>
> **Only create a new category if the new algorithms need different survey data from participants**
> (e.g. a new ranking mode, new per-item fields, or a new per-agent field).
> If the algorithms fit an existing category's input shape, add them to that category.
> The category determines what the survey collects — not the algorithm's internal complexity.

---

*After pasting this prompt, describe the specific algorithms you want to add and Claude will tell you which category they belong to (or whether a new one is needed), write the builder, register the algorithms, and update the templates.*
