"""Statistics used for EVERY decision. Pure functions, standard library only, fully deterministic.
An LLM never decides what won - this module does."""
import math
from statistics import NormalDist

_N = NormalDist()


def wilson(k, n, z=1.96):
    """95% interval for a rate k/n. Better than +-1.96*sqrt(p(1-p)/n) for small n or extreme p."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def two_prop(k1, n1, k2, n2):
    """Compare arm 1 against arm 2 (difference = p1 - p2).
    z-test with pooled variance (the right one for 'is there any difference').
    p_greater is one-sided: evidence that arm 1 is better than arm 2."""
    if n1 == 0 or n2 == 0:
        return dict(diff=0.0, z=0.0, p_two=1.0, p_greater=1.0, p_less=1.0, lo=-1.0, hi=1.0)
    p1, p2 = k1 / n1, k2 / n2
    pp = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    se_u = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)   # unpooled, for the interval
    d = p1 - p2
    return dict(diff=d, z=z, p_two=2 * (1 - _N.cdf(abs(z))), p_greater=1 - _N.cdf(z),
                p_less=_N.cdf(z), lo=d - 1.96 * se_u, hi=d + 1.96 * se_u)


def required_n_per_arm(p0, rel_lift, alpha=0.05, power=0.8):
    """Samples per arm needed to detect a relative lift (e.g. 0.10 = +10%) over base rate p0.
    Here n counts the denominator of the rate: CONNECTED calls for engagement, DIALS for pickup."""
    p1 = p0 * (1 + rel_lift)
    za, zb = _N.inv_cdf(1 - alpha / 2), _N.inv_cdf(power)
    var = p0 * (1 - p0) + p1 * (1 - p1)
    return math.ceil((za + zb) ** 2 * var / (p1 - p0) ** 2)


def benjamini_hochberg(pvals, q=0.1):
    """Which hypotheses to reject while keeping the expected share of false discoveries <= q.
    Needed because we test many (cohort x arm) pairs at once; without it ~5% of them 'win' by luck."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    cutoff = -1
    for rank, i in enumerate(order, 1):
        if pvals[i] <= q * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, i in enumerate(order, 1):
        if rank <= cutoff:
            keep[i] = True
    return keep


def non_inferior(k_new, n_new, k_ref, n_ref, margin, alpha=0.05):
    """Guardrail check: is the new arm's rate NOT worse than the reference by more than `margin`
    (absolute, e.g. 0.02)? True only when the one-sided lower bound of (new - ref) is above -margin.
    With little data the bound is wide, so we say False: no evidence of safety = not safe."""
    if n_new == 0 or n_ref == 0:
        return False
    p1, p2 = k_new / n_new, k_ref / n_ref
    se = math.sqrt(p1 * (1 - p1) / n_new + p2 * (1 - p2) / n_ref)
    return (p1 - p2) - _N.inv_cdf(1 - alpha) * se > -margin


def poisson_rate_excess(k, n, p0, alpha=0.01):
    """One-sided test: is the observed flag rate k/n significantly ABOVE a baseline rate p0?
    Normal approximation with a floor on counts, used for hallucination spikes. Returns p-value."""
    if n == 0:
        return 1.0
    mu = n * p0
    sd = math.sqrt(max(n * p0 * (1 - p0), 1e-9))
    return 1 - _N.cdf((k - 0.5 - mu) / sd)


def binom_sf(k, n, p):
    """Exact P(X >= k) for X ~ Binomial(n, p), computed in log space. For rare events (hallucination flags)
    the normal approximation is poor; this is exact and fast enough for n up to ~1e5."""
    if k <= 0:
        return 1.0
    if k > n or p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lp, lq, lg = math.log(p), math.log1p(-p), math.lgamma
    total = 0.0
    for i in range(k, n + 1):
        term = math.exp(lg(n + 1) - lg(i + 1) - lg(n - i + 1) + i * lp + (n - i) * lq)
        total += term
        if i > n * p and term < 1e-16 * total:
            break
    return min(1.0, total)