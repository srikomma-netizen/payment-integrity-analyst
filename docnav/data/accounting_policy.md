# Expense and Vendor Payment Policy (v3.2)

This policy is synthetic reference material for a demo. It is written to
resemble a real corporate accounts-payable policy, including the
cross-references that make flat chunk retrieval lose context.

## 1. Purpose and Scope

This policy governs the approval, processing, and payment of vendor invoices
and employee expenses for all legal entities. It applies to every employee,
contractor with purchasing authority, and member of the Accounts Payable (AP)
team. Where regional law is stricter, regional law prevails (see Appendix A).

## 2. Definitions

### 2.1 Approved Vendor

An Approved Vendor is a supplier that has completed onboarding under Section 4,
passed sanctions screening (Section 4.2), has a signed master agreement or
annual contract on file, and is marked ACTIVE in the vendor master. Vendors
inactive for more than 18 months lose Approved status automatically.

### 2.2 Purchase Order (PO)

A Purchase Order is a system-generated commitment document issued before goods
or services are received. A PO records the vendor, cost center, GL account,
quantity, unit price, and the approver chain required by Section 3.1.

### 2.3 Three-Way Match

A Three-Way Match is the reconciliation of (a) the PO, (b) the goods receipt or
service acceptance, and (c) the vendor invoice, within the tolerances in
Section 5.2. An invoice that passes the match may be released for payment
without further approval.

## 3. Spend Approval Thresholds

### 3.1 Standard Thresholds

All spend requires a PO before commitment, except as listed in Section 3.2.
Approval authority is cumulative by amount:

| Amount (USD)        | Required approver                      |
|---------------------|----------------------------------------|
| up to 5,000         | Cost center owner                      |
| 5,001 to 25,000     | Cost center owner + Department head    |
| 25,001 to 100,000   | Department head + Finance Director     |
| above 100,000       | Finance Director + CFO                 |

Invoices without a PO above 25,000 USD are placed on hold and routed to the
Finance Director. Splitting a purchase into several POs to stay under a
threshold is a policy violation and is reported under Section 9.

### 3.2 Exceptions to Thresholds

The following do not require a PO before commitment, provided the vendor is an
Approved Vendor as defined in Section 2.1:

- Software subscriptions and SaaS renewals up to 50,000 USD per year where an
  annual contract is on file. Above 50,000 USD a PO is required.
- Utilities, rent, and property taxes under an executed lease.
- Regulatory filing fees and statutory payments.
- Emergency repairs up to 10,000 USD, with a retrospective PO within 5 business days.

Exceptions do not remove the approval chain in Section 3.1; the approver signs
the invoice instead of the PO. Exceptions never apply to vendors that are not
Approved Vendors.

## 4. Vendor Onboarding

### 4.1 Required Documentation

Before a vendor can be paid, AP must hold: a completed vendor form, a tax form
(W-9 or W-8 series, or the local equivalent), a bank letter or voided check on
vendor letterhead, and the signed agreement. Documentation is retained for seven
years after the last payment.

### 4.2 Sanctions and Watchlist Screening

Every new vendor, and every existing vendor on each anniversary, is screened
against OFAC, EU, UN, and UK sanctions lists and the internal watchlist. A
potential match blocks payment until Compliance clears it in writing. Screening
results are stored with the vendor record.

### 4.3 Bank Detail Changes

A request to change vendor bank details is the highest-risk event in this
policy. The following controls are mandatory:

1. The change is verified by a call-back to a phone number already on file in
   the vendor master, never to a number provided in the change request.
2. Two AP staff approve the change independently (dual control).
3. The first payment to the new account is held for two business days.
4. An email-only request is treated as suspected fraud and escalated under
   Section 9 before any action is taken.

## 5. Invoice Processing

### 5.1 Three-Way Match Requirements

Invoices referencing a PO are matched per Section 2.3. If the match fails,
the invoice is parked and the PO owner is notified. The PO owner has 10
business days to resolve; unresolved invoices are escalated to the Finance
Director under Section 9.

### 5.2 Tolerances

A match passes automatically when the invoice amount is within the lower of
2 percent or 500 USD of the PO value, and quantity is within 5 percent. Any
variance above tolerance requires written approval from the PO owner, and
variances above 2,500 USD also require the next approver in the Section 3.1
chain.

### 5.3 Duplicate Invoice Controls

AP systems reject invoices with the same vendor, invoice number, and amount.
Near-duplicates (same vendor and amount within 30 days) are flagged for manual
review before payment.

## 6. Payment Terms and Early Payment

Standard terms are net 45 from invoice receipt. Early payment is permitted only
under an approved early-payment discount of at least 1 percent, or where
Treasury confirms a cash-position benefit in writing. Payment runs occur twice
weekly; urgent payments outside a run require Treasury approval.

## 7. Travel and Entertainment

### 7.1 Per Diem

Domestic travel per diem is 75 USD per day for meals and incidentals.
International travel per diem is 110 USD per day, or the published government
rate for the destination city if higher. Per diem is not paid on days when the
company provides meals, and is reduced by 50 percent on travel days.

### 7.2 Non-Reimbursable Items

The following are never reimbursed: alcohol outside approved client
entertainment, traffic and parking fines, personal entertainment, upgrades
above the booked class, expenses for companions, and any item without an
itemized receipt above 25 USD.

## 8. Month-End Close

### 8.1 Accrual Cutoff

The GL closes on the third business day after month end. Services received but
not invoiced by the cutoff are accrued by the cost center owner using the PO
value or a documented estimate. Accruals above 10,000 USD require Finance
review before posting.

### 8.2 Late Invoices

An invoice received after the close for services in a prior period is booked to
the current period, and the prior-period accrual from Section 8.1 is reversed
in the same entry. If no accrual was raised, the cost center owner documents
the omission and AP reports it in the monthly close exceptions log.

## 9. Exceptions and Escalation

Any deviation from this policy requires a written exception approved by the
Finance Director, logged in the exceptions register with a business reason and
an expiry date. Suspected fraud, including unverified bank changes (Section 4.3)
and PO splitting (Section 3.1), is escalated immediately to Internal Audit and
Compliance. No payment is made while an escalation is open.

## Appendix A. Regional Overrides

- EMEA: statutory payment terms cap at net 30 for SMEs; Section 6 terms are
  shortened accordingly.
- APAC: withholding tax certificates must be received before first payment.
- NA: no overrides.
