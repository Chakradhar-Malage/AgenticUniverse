"""How often does our decision rule crown a false winner? Measured, not guessed.

Simulates experiments where we KNOW the truth (arms identical, or one arm truly better), applies
the same rule the controller will use, and counts mistakes. Our own numbers, our own random data,
nothing from the simulator.   python -m controller.montecarlo
"""
import numpy as np

from .stats import benjamini_hochberg, required_n_per_arm, two_prop


def run(p=0.55, n=1500, arms=4, lift=0.0, trials=2000, alpha=0.05, correction="bh", seed=1):
    """Arm 0 = control. If lift>0, arm 1 is truly better by that relative lift.
    Returns (share of trials with >=1 declared winner, share where the TRUE arm 1 was found)."""
    rng = np.random.default_rng(seed)
    probs = np.full(arms, p)
    if lift:
        probs[1] = p * (1 + lift)
    any_win = found = 0
    for _ in range(trials):
        k = rng.binomial(n, probs)
        pv = [two_prop(int(k[i]), n, int(k[0]), n)["p_greater"] for i in range(1, arms)]
        win = benjamini_hochberg(pv, q=alpha) if correction == "bh" else [x <= alpha for x in pv]
        any_win += any(win)
        found += win[0]
    return any_win / trials, found / trials


if __name__ == "__main__":
    n_need = required_n_per_arm(0.55, 0.10)
    print(f"samples/arm to detect +10% on a 0.55 rate (80% power): {n_need}\n")
    print("Null world (all arms identical): how often do we crown a false winner? (3 candidates vs control)")
    for corr in ("none", "bh"):
        fw, _ = run(n=n_need, lift=0.0, correction=corr)
        print(f"   correction={corr:<5} false-winner rate = {fw:.3f}")
    print("\nReal +10% arm: how often do we find it (power)?")
    for n in (500, 1000, n_need, 2 * n_need):
        _, pw = run(n=n, lift=0.10)
        print(f"   n/arm={n:<5} power = {pw:.2f}")