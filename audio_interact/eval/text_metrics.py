from __future__ import annotations


def cer(reference: str, hypothesis: str) -> float:
    ref = list(reference or "")
    hyp = list(hypothesis or "")
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, ref_ch in enumerate(ref, start=1):
        curr = [i]
        for j, hyp_ch in enumerate(hyp, start=1):
            cost = 0 if ref_ch == hyp_ch else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1] / len(ref)

