# Payment Integrity Investigation Policy (v5.1)

This policy is synthetic reference material for a demo. It is written to
resemble a real payment-integrity policy, including the cross-references
that make flat chunk retrieval lose context.

## 1. Purpose and Scope

This policy governs the detection, investigation, and disposition of
suspicious healthcare claims, duplicate payments, coding anomalies, and
vendor payment diversion. It applies to the payment-integrity team, the
special investigations unit (SIU), accounts payable, and any automated
tooling that assists investigators. Automated output is advisory; the
decisions in Section 6 are made by people.

## 2. Definitions

### 2.1 Duplicate Payment

A Duplicate Payment is more than one disbursement against a single
adjudicated claim, regardless of how the second disbursement was triggered.
Resubmissions caused by portal timeouts are a known source and are not
exempt from review. The disposition rules are in Section 5.2.

### 2.2 Upcoding Indicator

An Upcoding Indicator exists when an evaluation and management claim is
billed at a level the documentation does not support. The quantitative
test is a billed amount above the peer median for the same procedure code
by a robust z-score of 4 or more, as computed by the payment-integrity
model. Severity follows Section 3.1.

### 2.3 Unbundling

Unbundling is billing the component codes of a panel separately on the
same service date for the same member and provider when a bundled code
exists. Recognized panel pairs are listed in Appendix A.

### 2.4 Confirmed Case

A Confirmed Case is an investigation closed with the outcome "confirmed"
by an investigator within the prior 24 months. Confirmed cases change
severity under Section 3.2.

### 2.5 Approved Vendor

An Approved Vendor has completed onboarding, passed sanctions screening,
has a signed agreement on file, and is marked ACTIVE in the vendor master.
Vendor controls in Section 4 apply only to Approved Vendors; payments to
any other vendor are held.

## 3. Detection and Severity

### 3.1 Rule Severity Table

Upstream rules raise typed signals. Base severity by rule:

| Rule | Signal                   | Base severity |
|------|--------------------------|---------------|
| R1   | duplicate payment        | high          |
| R2   | amount outlier (Section 2.2) | medium    |
| R3   | unbundling (Section 2.3) | medium        |
| R4   | prior confirmed case     | medium        |
| R5   | unverified bank change   | high          |

Two or more medium signals on one claim are treated as high. Severity
after adjustment under Section 3.2 determines the review path in
Section 6.

### 3.2 Prior-Case Adjustments

A prior false-positive disposition for the same member, provider, or
vendor and the same pattern lowers severity by one level unless new
evidence contradicts it. A Confirmed Case as defined in Section 2.4 raises
severity by one level. Adjustments are recorded in the case summary with
the prior case identifier.

## 4. Vendor Payment Controls

### 4.1 Bank Detail Changes

A change to vendor bank details must be verified by a call-back to a phone
number already held in the vendor master, never to a number supplied with
the change request. Email-only change requests are treated as suspected
payment diversion. A payment to a vendor whose bank details changed
without a verified call-back within the prior 30 days is a high-severity
finding and is held for investigator review under Section 6.

### 4.2 Invoice Controls

Vendor invoices above 25,000 USD require a matching purchase order. An
invoice that is both above 25,000 USD and follows an unverified bank change
is escalated to the SIU lead, not only an investigator, and no payment is
released while the escalation is open.

## 5. Investigation Standards

### 5.1 Evidence Standard

Every finding in a case summary must cite the identifiers of the records
that support it: claim, payment, flag, prior case, or vendor event
identifiers. A finding without identifiers is incomplete and must not be
escalated. Policy citations use the section number of this document.

### 5.2 Duplicate Payment Disposition

For a Duplicate Payment as defined in Section 2.1: if a reversal already
exists, close with the reversal identifier. If no reversal exists and the
second payment is under the recovery threshold in Section 8.1, raise a
recovery request and close without approval. At or above the threshold,
or where the provider has a Confirmed Case, obtain investigator approval
under Section 6 before any recovery letter is issued.

### 5.3 Coding Findings

A single upcoding indicator is a medium-severity finding. Two indicators,
or one indicator with a Confirmed Case, is high severity and requires
investigator review before provider outreach under Section 8.2. Unbundling
of a pair in Appendix A is repriced to the bundled code with an education
letter unless a Confirmed Case exists; see Section 2.3 for the definition.

## 6. Review Paths and Human Approval

Flagged cases are routed by adjusted severity and confidence:

- Low severity with high confidence: automated handling, closed with a
  logged rationale.
- Medium severity: analyst queue; the analyst may close or escalate.
- High severity, low automated confidence, missing evidence, or a summary
  that fails verification: held for investigator review before any
  outbound action.

An investigator must approve before issuing a recovery letter at or above
the Section 8.1 threshold, suspending a provider or vendor, or releasing a
held payment. The reviewer, decision, and rationale are recorded in the
case audit trail.

## 7. PHI Handling

### 7.1 Minimum Necessary

Analysts work on pseudonymous member identifiers only. Investigators may
resolve name, medical record number, and date of birth for a case they are
assigned. Full identifiers are limited to the SIU lead. Every resolution
is logged with the requesting role.

### 7.2 Model Boundary

No protected health information may be sent to a language model or
analytics assistant. Records are de-identified before retrieval,
querying, or summarization, and re-identification happens only in the
application layer against the requesting role's clearance under
Section 7.1.

## 8. Recovery

### 8.1 Recovery Thresholds

Recovery requests under 1,000 USD may be raised and closed by the analyst
queue. Requests at or above 1,000 USD require investigator approval under
Section 6. Requests above 25,000 USD also require SIU lead sign-off.

### 8.2 Provider Outreach

Outreach to a provider about a coding finding requires the case summary to
meet the evidence standard in Section 5.1 and, for high-severity findings,
investigator approval. Outreach is suspended while a Confirmed Case for the
same provider is under appeal.

## 9. Feedback and Exceptions

Every investigator disposition, including overrides of automated
recommendations, is captured with the evidence and recommendation it
overrode and feeds the quarterly review of rules and retrieval quality.
Any deviation from this policy requires a written exception approved by
the SIU lead, logged with a business reason and an expiry date.

## Appendix A. Recognized Panel Pairs

| Components      | Bundled code |
|-----------------|--------------|
| 80053 + 85025   | 80050        |
| 80048 + 80061   | 80053 (partial) |
