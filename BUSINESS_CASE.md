# Business case: checking invoices before they are paid

## The problem

An AP clerk opens each emailed invoice, finds the PO, checks the sums, checks whether the goods arrived, checks it hasn't been paid already, and keys it into the ERP. Most invoices are fine, so the work is repetitive. The few that aren't fine are the expensive ones:

- **Duplicates.** The same invoice sent twice, or resent under a new number.
- **Bank-detail fraud.** An email from a look-alike domain asking for payment to a "new" account.
- **Billing ahead of delivery.** The whole PO invoiced when only part has arrived.
- **Creeping overspend.** Several invoices on one PO that are each fine alone but go over the PO together.

A tired person on a busy day misses these. A tool that only extracts fields doesn't catch them either, because each one needs context: earlier invoices, receipts, the vendor master.

## What this does

| Before | After |
|---|---|
| A person reads every invoice | The rules read text PDFs. Claude reads them independently as a second check. A person only reads scans and anything unclear |
| A PO match is done by eye | A running total per PO, a tolerance applied once per PO, and goods receipts compared with billing (three-way match) |
| Duplicates are caught if someone remembers | Every invoice is compared with earlier ones: same number, or same amount on the same PO close in time |
| Bank changes are spotted by chance | The bank account and the sender's domain are compared with the vendor master on every invoice |
| A hold means writing an email from scratch | The next step names who acts and what to ask for, with the exact figures, and a drafted message is one click away |
| A spreadsheet is retyped into the ERP | Approved invoices export as a payment file that only ever uses the bank account on file |

## Design choices a buyer will ask about

- **Rules decide, not AI.** The same answer every time, explained line by line. Audit can read it.
- **AI can stop money, never move it.** Claude's readings only ever add a hold: a disagreement holds, an unconfirmed scan holds. Its absence never approves anything.
- **Reject only when certain.** Blocked vendor, cancelled PO, exact duplicate. A wrong hold costs a minute. A wrong reject damages a vendor relationship.
- **Nothing approves unless every required check ran.** A check that couldn't run is itself a hold.

## How the value is measured

The overview estimates time given back from two assumptions in `data/policy.json`: 12 minutes per invoice by hand, and 5 minutes to settle a hold. **These are placeholders, not measurements.** In a pilot, replace them with the team's own timings.

Measure in a pilot:

1. **Straight-through rate.** The share of invoices approved with no one touching them.
2. **False holds.** Holds a person approves unchanged. This is the tuning target.
3. **Misses.** A sample of approved invoices audited by hand every week. This is the safety target, and it has to be zero before anyone trusts it more.
4. **Cycle time.** From email received to approved.
5. **Stopped payments.** Duplicates, bank changes and over-billing caught, by value.

## Rollout

1. **Shadow mode, 2–4 weeks.** It runs beside the clerks and decides nothing. Compare its decisions with theirs.
2. **Assisted.** Holds and drafted messages go to the clerks. Approvals still get a person's click.
3. **Straight-through, within limits.** Auto-approve below a value threshold for vendors with a clean history. Keep auditing a weekly sample.

## What it does not do yet

- No line-item matching: receipts are compared by value, not item by item.
- One currency, one company, no tax engine.
- The field reader is tested on three invoice layouts. New layouts mostly hold rather than misread, but that needs proving on real volume. That's what shadow mode is for.
- No logins, roles or approval limits per person.
- The inbox is a folder of email files standing in for a real mailbox connection.
