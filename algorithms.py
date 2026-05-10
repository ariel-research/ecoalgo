"""
Algorithm registry for the survey app.

To add a new algorithm from an existing library:
    Add one entry to ALGORITHMS pointing to the right builder and module/function.

To add a new library:
    Write a build_<lib>_instance(survey) helper and add entries to ALGORITHMS.
"""

CATEGORIES = {
    'fair_division':           'Fair Division',
    'capacitated_allocation':  'Capacitated & Weighted Allocation',
    'budget_allocation':       'Budget Allocation',
    'approval_voting':         'Approval Voting',
    'participatory_budgeting': 'Participatory Budgeting',
}


# ── Builder helpers ────────────────────────────────────────────────────────────
#
# Each builder receives a Survey ORM object and returns the input object
# expected by the algorithm.  fairpyx builders return an Instance;
# abcvoting builders return a Profile.




def _survey_to_valuations(survey):
    items = survey.items.all()
    valuations = {}
    for p in survey.participants.all():
        name = p.get_display_name()
        valuations[name] = {}
        for ranking in p.rankings.all():
            if survey.ranking_mode == 'ordinal':
                value = len(items) - ranking.rank + 1
            elif survey.ranking_mode in ('budget', 'points'):
                value = ranking.points
            else:
                value = ranking.rating
            valuations[name][ranking.item.name] = value
    return valuations


def _get_item_capacities(survey):
    if survey.use_item_capacity:
        return {item.name: item.capacity for item in survey.items.all()}
    n = survey.participants.count()
    return {item.name: max(n, 1) for item in survey.items.all()}


def build_standard_instance(survey):
    """fairpyx Instance with valuations + item capacities."""
    from fairpyx import Instance
    return Instance(
        valuations=_survey_to_valuations(survey),
        item_capacities=_get_item_capacities(survey),
    )


def build_capacitated_instance(survey):
    """fairpyx Instance that also passes agent capacities and weights.
    Used by algorithms designed for multi-seat / weighted allocation."""
    from fairpyx import Instance
    participants = survey.participants.all()

    agent_capacities = None
    if survey.require_user_capacity:
        caps = {p.get_display_name(): p.user_capacity
                for p in participants if p.user_capacity is not None}
        if caps:
            agent_capacities = caps

    agent_weights = None
    if survey.use_weights:
        weights = {p.get_display_name(): p.user_weight
                   for p in participants if p.user_weight is not None}
        if weights:
            agent_weights = weights

    return Instance(
        valuations=_survey_to_valuations(survey),
        item_capacities=_get_item_capacities(survey),
        agent_capacities=agent_capacities,
        agent_target_weights=agent_weights,
    )


def build_approval_profile(survey):
    """abcvoting Profile: each participant maps to the set of items they approved (points == 1)."""
    from abcvoting import preferences as abcpref
    items = list(survey.items.all())
    item_to_idx = {item.id: idx for idx, item in enumerate(items)}
    profile = abcpref.Profile(
        num_cand=len(items),
        cand_names=[item.name for item in items],
    )
    for p in survey.participants.all():
        approved = {item_to_idx[r.item_id]
                    for r in p.rankings.all()
                    if r.points and r.points > 0}
        profile.add_voter(approved)
    return profile


def build_pabutools_instance_and_profile(survey):
    """pabutools (Instance, ApprovalProfile) from survey data.
    item.weight → project cost; survey.total_points → budget limit."""
    from pabutools.election import Project, Instance, ApprovalProfile, ApprovalBallot
    from fractions import Fraction

    items = list(survey.items.all())
    projects = [Project(item.name, cost=Fraction(str(item.weight))) for item in items]
    project_by_name = {p.name: p for p in projects}

    instance = Instance(projects, budget_limit=Fraction(str(survey.total_points)))

    profile = ApprovalProfile()
    for p in survey.participants.all():
        approved = {project_by_name[r.item.name]
                    for r in p.rankings.all()
                    if r.points and r.points > 0 and r.item.name in project_by_name}
        profile.append(ApprovalBallot(approved))

    return instance, profile


# ── Algorithm registry ─────────────────────────────────────────────────────────

ALGORITHMS = {

    # ── Fair Division ──────────────────────────────────────────────────────────

    'round_robin': {
        'category':     'fair_division',
        'display_name': 'Round Robin',
        'group':        'Picking Sequence',
        'description':  'Agents take turns picking their most-preferred available item in a fixed cyclic order.',
        'module':       'fairpyx.algorithms.picking_sequence',
        'function':     'round_robin',
        'builder':      build_standard_instance,
    },
    'bidirectional_round_robin': {
        'category':     'fair_division',
        'display_name': 'Bidirectional Round Robin',
        'group':        'Picking Sequence',
        'description':  'Like Round Robin but the picking order reverses each round (1-2-3-3-2-1…), reducing first-mover advantage.',
        'module':       'fairpyx.algorithms.picking_sequence',
        'function':     'bidirectional_round_robin',
        'builder':      build_standard_instance,
    },
    'serial_dictatorship': {
        'category':     'fair_division',
        'display_name': 'Serial Dictatorship',
        'group':        'Picking Sequence',
        'description':  'Agents pick in a fixed priority order; each agent selects all items they want before the next agent picks.',
        'module':       'fairpyx.algorithms.picking_sequence',
        'function':     'serial_dictatorship',
        'builder':      build_standard_instance,
    },
    'utilitarian_matching': {
        'category':     'fair_division',
        'display_name': 'Utilitarian Matching',
        'group':        'Matching',
        'description':  'Finds the assignment that maximises total welfare (sum of all agents\' values for their allocated items).',
        'module':       'fairpyx.algorithms.utilitarian_matching',
        'function':     'utilitarian_matching',
        'builder':      build_standard_instance,
    },
    'almost_egalitarian_allocation': {
        'category':     'fair_division',
        'display_name': 'Almost Egalitarian',
        'group':        'Egalitarian',
        'description':  'Maximises the minimum value received by any agent (leximin objective).',
        'module':       'fairpyx.algorithms.almost_egalitarian',
        'function':     'almost_egalitarian_allocation',
        'builder':      build_standard_instance,
    },
    'almost_egalitarian_without_donation': {
        'category':     'fair_division',
        'display_name': 'Almost Egalitarian (No Donation)',
        'group':        'Egalitarian',
        'description':  'Egalitarian variant where agents may not donate items to others.',
        'module':       'fairpyx.algorithms.almost_egalitarian',
        'function':     'almost_egalitarian_without_donation',
        'builder':      build_standard_instance,
    },
    'almost_egalitarian_with_donation': {
        'category':     'fair_division',
        'display_name': 'Almost Egalitarian (With Donation)',
        'group':        'Egalitarian',
        'description':  'Egalitarian variant where agents may donate items to improve the worst-off agent\'s share.',
        'module':       'fairpyx.algorithms.almost_egalitarian',
        'function':     'almost_egalitarian_with_donation',
        'builder':      build_standard_instance,
    },
    'fractional_egalitarian_allocation': {
        'category':     'fair_division',
        'display_name': 'Fractional Egalitarian',
        'group':        'Fractional Egalitarian',
        'description':  'Computes a fractional (possibly shared) allocation that equalises values across agents as much as possible.',
        'module':       'fairpyx.algorithms.fractional_egalitarian',
        'function':     'fractional_egalitarian_allocation',
        'builder':      build_standard_instance,
    },
    'fractional_egalitarian_utilitarian_allocation': {
        'category':     'fair_division',
        'display_name': 'Fractional Egalitarian-Utilitarian',
        'group':        'Fractional Egalitarian',
        'description':  'Balances egalitarian fairness with utilitarian efficiency in a fractional allocation.',
        'module':       'fairpyx.algorithms.fractional_egalitarian',
        'function':     'fractional_egalitarian_utilitarian_allocation',
        'builder':      build_standard_instance,
    },
    'maximally_proportional_allocation': {
        'category':     'fair_division',
        'display_name': 'Maximally Proportional',
        'group':        'Proportionality',
        'description':  'Finds the allocation closest to giving every agent exactly 1/n of the total available value.',
        'module':       'fairpyx.algorithms.maximally_proportional',
        'function':     'maximally_proportional_allocation',
        'builder':      build_standard_instance,
    },
    'gale_shapley': {
        'category':     'fair_division',
        'display_name': 'Gale-Shapley',
        'group':        'Market Mechanisms',
        'description':  'Pareto-dominant market mechanism based on the classic deferred-acceptance algorithm.',
        'module':       'fairpyx.algorithms.Gale_Shapley_pareto_dominant_market_mechanism',
        'function':     'gale_shapley',
        'builder':      build_standard_instance,
    },
    'OC_function': {
        'category':     'fair_division',
        'display_name': 'Ordinal/Cardinal (OC)',
        'group':        'Optimization-based',
        'description':  'Optimization mechanism that combines ordinal rankings with cardinal values.',
        'module':       'fairpyx.algorithms.Optimization_based_Mechanisms',
        'function':     'OC_function',
        'builder':      build_standard_instance,
    },
    'TTC_function': {
        'category':     'fair_division',
        'display_name': 'Top Trading Cycles (TTC)',
        'group':        'Optimization-based',
        'description':  'Optimization-based variant of the Top Trading Cycles mechanism.',
        'module':       'fairpyx.algorithms.Optimization_based_Mechanisms',
        'function':     'TTC_function',
        'builder':      build_standard_instance,
    },
    'TTC_O_function': {
        'category':     'fair_division',
        'display_name': 'TTC Optimized (TTC-O)',
        'group':        'Optimization-based',
        'description':  'Welfare-optimized variant of Top Trading Cycles.',
        'module':       'fairpyx.algorithms.Optimization_based_Mechanisms',
        'function':     'TTC_O_function',
        'builder':      build_standard_instance,
    },
    'SP_function': {
        'category':     'fair_division',
        'display_name': 'Second Price (SP)',
        'group':        'Optimization-based',
        'description':  'Second-price auction mechanism adapted for fair division.',
        'module':       'fairpyx.algorithms.Optimization_based_Mechanisms',
        'function':     'SP_function',
        'builder':      build_standard_instance,
    },
    'SP_O_function': {
        'category':     'fair_division',
        'display_name': 'Second Price Optimized (SP-O)',
        'group':        'Optimization-based',
        'description':  'Welfare-optimized variant of the Second Price mechanism.',
        'module':       'fairpyx.algorithms.Optimization_based_Mechanisms',
        'function':     'SP_O_function',
        'builder':      build_standard_instance,
    },

    # ── Capacitated & Weighted Allocation ──────────────────────────────────────

    'iterated_maximum_matching': {
        'category':     'capacitated_allocation',
        'display_name': 'Iterated Maximum Matching',
        'group':        'Matching',
        'description':  'Repeatedly finds maximum-weight matchings. Designed for settings where agents have capacity limits and/or weights (e.g. course allocation).',
        'module':       'fairpyx.algorithms.iterated_maximum_matching',
        'function':     'iterated_maximum_matching',
        'builder':      build_capacitated_instance,
    },
    'iterated_maximum_matching_adjusted': {
        'category':     'capacitated_allocation',
        'display_name': 'Iterated Maximum Matching (Adjusted)',
        'group':        'Matching',
        'description':  'Adjusted variant of Iterated Maximum Matching with modified weight normalisation between rounds.',
        'module':       'fairpyx.algorithms.iterated_maximum_matching',
        'function':     'iterated_maximum_matching_adjusted',
        'builder':      build_capacitated_instance,
    },
    'iterated_maximum_matching_unadjusted': {
        'category':     'capacitated_allocation',
        'display_name': 'Iterated Maximum Matching (Unadjusted)',
        'group':        'Matching',
        'description':  'Unadjusted variant of Iterated Maximum Matching using raw valuation weights across all rounds.',
        'module':       'fairpyx.algorithms.iterated_maximum_matching',
        'function':     'iterated_maximum_matching_unadjusted',
        'builder':      build_capacitated_instance,
    },

    # ── Approval Voting ────────────────────────────────────────────────────────

    'pav': {
        'category':     'approval_voting',
        'display_name': 'Proportional Approval Voting (PAV)',
        'group':        'Proportional Rules',
        'description':  'Selects a winning committee by maximising a proportional score; voters get diminishing credit for each additional approved committee member.',
        'runner':       'abcvoting',
        'rule_id':      'pav',
        'builder':      build_approval_profile,
    },
    'seqpav': {
        'category':     'approval_voting',
        'display_name': 'Sequential PAV',
        'group':        'Sequential Rules',
        'description':  'Greedy sequential variant of PAV: adds one candidate at a time, choosing whichever maximises the PAV score increment.',
        'runner':       'abcvoting',
        'rule_id':      'seqpav',
        'builder':      build_approval_profile,
    },
    'seqcc': {
        'category':     'approval_voting',
        'display_name': 'Sequential Chamberlin-Courant (SeqCC)',
        'group':        'Sequential Rules',
        'description':  'Greedy sequential variant of the Chamberlin-Courant rule: adds the candidate that maximises the number of voters who have at least one approved committee member.',
        'runner':       'abcvoting',
        'rule_id':      'seqcc',
        'builder':      build_approval_profile,
    },
    'seqphragmen': {
        'category':     'approval_voting',
        'display_name': 'Sequential Phragmén',
        'group':        'Sequential Rules',
        'description':  'Load-balancing rule: seats are allocated one by one to the candidate whose supporters carry the least accumulated load, promoting proportional representation.',
        'runner':       'abcvoting',
        'rule_id':      'seqphragmen',
        'builder':      build_approval_profile,
    },
    'equal_shares': {
        'category':     'approval_voting',
        'display_name': 'Method of Equal Shares (Rule X)',
        'group':        'Budget-Based Rules',
        'description':  'Gives each voter an equal virtual budget; candidates are elected when their supporters collectively afford them. A completion method fills any remaining seats.',
        'runner':       'abcvoting',
        'rule_id':      'equal-shares',
        'builder':      build_approval_profile,
        'extra_params': ['completion'],
    },

    # ── Participatory Budgeting ────────────────────────────────────────────────
    # All four use an ApprovalProfile + Instance(projects with costs, budget_limit).
    # sequential_phragmen needs no satisfaction measure; the other three do
    # (Cardinality_Sat is injected by the runner when needs_sat_class=True).

    'pb_seq_phragmen': {
        'category':     'participatory_budgeting',
        'display_name': 'Sequential Phragmén',
        'group':        'Phragmén Rules',
        'description':  'Load-balancing rule: projects are elected one by one by minimising the virtual debt carried by supporters, promoting proportional representation across voters.',
        'runner':       'pabutools',
        'module':       'pabutools.rules',
        'function':     'sequential_phragmen',
        'builder':      build_pabutools_instance_and_profile,
        'needs_sat_class': False,
    },
    'pb_equal_shares': {
        'category':     'participatory_budgeting',
        'display_name': 'Method of Equal Shares',
        'group':        'Equal Shares',
        'description':  'Distributes the budget equally among voters; projects are funded when their supporters can collectively afford them from their remaining share.',
        'runner':       'pabutools',
        'module':       'pabutools.rules',
        'function':     'method_of_equal_shares',
        'builder':      build_pabutools_instance_and_profile,
        'needs_sat_class': True,
    },
    'pb_max_welfare': {
        'category':     'participatory_budgeting',
        'display_name': 'Max Additive Utilitarian Welfare',
        'group':        'Welfare Maximisation',
        'description':  'Selects the set of projects within budget that maximises total voter satisfaction (sum of approved funded projects across all voters).',
        'runner':       'pabutools',
        'module':       'pabutools.rules',
        'function':     'max_additive_utilitarian_welfare',
        'builder':      build_pabutools_instance_and_profile,
        'needs_sat_class': True,
    },
    'pb_greedy_welfare': {
        'category':     'participatory_budgeting',
        'display_name': 'Greedy Utilitarian Welfare',
        'group':        'Welfare Maximisation',
        'description':  'Greedily selects projects in order of approval-count-to-cost ratio until the budget is exhausted.',
        'runner':       'pabutools',
        'module':       'pabutools.rules',
        'function':     'greedy_utilitarian_welfare',
        'builder':      build_pabutools_instance_and_profile,
        'needs_sat_class': True,
    },
}


def get_algo_data_for_template():
    """Return a dict structured for the frontend category → algorithm selector."""
    data = {}
    for cat_key, cat_label in CATEGORIES.items():
        algos = [
            {
                'value':       name,
                'label':       entry['display_name'],
                'group':       entry.get('group', ''),
                'description': entry.get('description', ''),
            }
            for name, entry in ALGORITHMS.items()
            if entry['category'] == cat_key
        ]
        if algos:
            data[cat_key] = {'label': cat_label, 'algorithms': algos}
    return data
