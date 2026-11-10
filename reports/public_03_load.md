# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.364 | +6.7% | 0.262 | +7.3% | 0.497 | -0.6% | 0% | 409 (408) |
| arav_kyc_1 | 0.452 | +27.6% | 0.262 | +22.1% | 0.595 | -0.2% | 0% | 393 (370) |
| arav_renewal_0 | 0.428 | +6.4% | 0.280 | +5.8% | 0.545 | +0.4% | 0% | 449 (440) |
| kest_cancel_2 | 0.364 | +7.5% | 0.261 | +7.1% | 0.500 | +0.2% | 0% | 433 (411) |
| kest_kyc_1 | 0.490 | +38.8% | 0.282 | +32.0% | 0.593 | -0.1% | 0% | 441 (339) |
| kest_renewal_0 | 0.432 | +8.2% | 0.280 | +6.1% | 0.548 | +1.1% | 0% | 426 (433) |
| nort_cancel_2 | 0.365 | +7.6% | 0.262 | +8.2% | 0.496 | -0.6% | 0% | 396 (393) |
| nort_kyc_1 | 0.483 | +36.4% | 0.280 | +31.1% | 0.592 | -0.5% | 0% | 454 (323) |
| nort_renewal_0 | 0.428 | +5.9% | 0.280 | +6.4% | 0.545 | -0.6% | 0% | 444 (411) |
| tide_cancel_2 | 0.366 | +7.9% | 0.262 | +7.1% | 0.499 | -0.0% | 0% | 428 (407) |
| tide_kyc_1 | 0.493 | +39.5% | 0.285 | +32.7% | 0.593 | +0.3% | 0% | 471 (356) |
| tide_renewal_0 | 0.433 | +8.0% | 0.281 | +6.2% | 0.549 | +1.3% | 0% | 447 (438) |
| ALL | 0.425 | +16.4% | 0.273 | +13.5% | 0.547 | +0.6% | 0% | 5191 (4729) |

## Changes applied by the controller

28 applied — harmful: 4, beneficial: 20, neutral: 4

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.044 → **harmful**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.044 → **harmful**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.044 → **harmful**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.044 → **harmful**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.005 → **neutral**
- nort_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.005 → **neutral**
- kest_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.005 → **neutral**
- arav_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.005 → **neutral**
- tide_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- nort_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.101 → **beneficial**
- kest_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.101 → **beneficial**
- arav_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.101 → **beneficial**
- tide_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.101 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.353 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.366 → **beneficial**
- tide_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.433 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.122 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.444 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.126 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.119 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.098 → **beneficial**

## Governance

```
{
  "approval_requests": 10,
  "decisions": {
    "approved": 8,
    "rejected": 2,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 2
  },
  "applied_via_approval": 8,
  "auto_applied": 20,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_kyc_1": 1,
    "kest_kyc_1": 1,
    "arav_kyc_1": 1,
    "tide_kyc_1": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0