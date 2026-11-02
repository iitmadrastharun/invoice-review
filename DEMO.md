# Five-minute walkthrough

Everything here was run in the app. Say it in your own words; these are prompts, not a script.

**Before recording:** see the checklist at the bottom. Rehearse once the same day. Claude's readings are cached from the rehearsal, so the video won't wait on them; the cross-check stage then says "Cached reading", which is true, so say so. A fresh reading takes about 25 seconds and runs in the background while earlier invoices are checked.

| Time | Do | Say |
|---|---|---|
| 0:00 | **Overview** | An AP clerk opens every invoice, finds the PO, checks the sums and whether the goods arrived, and keys it in. Most are fine; the few that aren't are expensive: duplicates, bank-detail fraud, billing ahead of delivery. The rule here: rules decide, AI reads and double-checks, and nothing is approved unless every check ran. |
| 0:30 | **Inbox**, then **Check all 9 new emails** | Nine vendor emails. They're checked one at a time in arrival order, so each sees the decisions before it. Watch the board fill in. |
| 1:10 | Open **S6** (Kaveri Industrial, "URGENT: updated bank details") | This is the classic fraud. The email comes from kaveri-industrial-**billing**, not Kaveri's domain on file, and it asks for payment to a new account. Two holds. Click **Draft it**: it's a call note to the number on file, never a reply to that email. |
| 1:55 | Open **S2** (Meenakshi Facility) | Fine on its own: ₹8,10,000 against a ₹40,00,000 PO. But two earlier invoices already used ₹32,50,000, so it goes ₹35,000 over the limit. Show the sums at stage 9. **Draft it**: an email to the buyer, M. Okafor, with the exact figures. |
| 2:35 | Open **S3** (Ashoka Packaging resend) | A different number, so a PO match alone would pay it twice. The duplicate check catches it, and receiving booked only one shipment. Show the earlier invoice side by side. |
| 3:05 | Open **S4** (scan) | No text in it. Claude read it, but an AI reading is never trusted to move money: the clerk checks the form against the page and presses **Re-run**. All 12 checks pass and it's approved, with the first hold still on record. |
| 3:55 | Point at the **AI cross-check** stage on any text invoice | On every text PDF, Claude reads the page independently. If the two readings disagree, it holds. That's the guard against the rules misreading a layout they haven't seen. AI can stop a payment; it can't make one. |
| 4:15 | **Overview** again | The five steps with real counts, what's waiting and who acts, why invoices stop. **Download CSV** is the payment file, and it pays only to the bank account on file. |
| 4:40 | Limits | Tested on three invoice layouts I made. Receipts are matched by value, not line by line. The hosted link has no AI, so scans there go to a blank form and there's no cross-check. In a real rollout I'd run it in shadow mode first. |

**If Claude fails live:** say so. A failed scan read gets a blank form and the same hold; a failed cross-check is noted and the rules still decide. The process carries on.

**Keep for questions:**
- **"Can a clerk just type the right number?"** On S6, click "This reading is wrong" on Bank last 4, type 4471, re-run. It still holds (`H-OVERRIDE`), and only a final decision with a note gets it through.
- **S9:** the whole PO billed, only two of three pallets received. It holds and says bill ₹3,20,000 now and the rest on delivery.
- **S7:** GST inside the total. It works out the net from the printed 18% and approves.
- **S8:** no PO printed. It suggests PO-4950 but never assumes it. Enter it and it approves.
- **S5:** no invoice number, so it holds and asks for it.
- **Batch upload:** drop several PDFs on **Check invoices**.

## Why the choices are what they are

- **Rules decide, not AI.** A rule gives the same answer every time and can be explained line by line. AI is used where no rule can work (reading an image), as an independent second reader, and to word messages. It can only add holds.
- **Reject is rare.** A wrong hold costs a minute; a wrong reject damages a vendor relationship. Reject is only for certain cases: blocked vendor, cancelled PO, exact duplicate.
- **No confidence score.** Nobody can defend a threshold like 87%. Each field is found, missing or conflicting, and a clerk can check that against the page.
- **The bank account and the sender are checked against the master, never the invoice.** The email and the PDF are what an attacker controls.

## Limits and assumptions to be upfront about

- **Field reading:** tested on three layouts built for this demo. The AI cross-check lowers the risk of a misread getting through on a text PDF, but the hosted version runs without it.
- **Receipts:** matched by value per PO, not item by item.
- **Scans:** a clerk who mistypes a value could get through. A real deployment would add a second approver above a threshold.
- **Duplicates:** a resend with an unrelated number, more than 14 days apart, on a different PO won't be caught. A statement reconciliation would catch it.
- **Sender check:** compares email domains only, and only for invoices that came by email.
- **Assumptions:** tolerance is 2% of the PO, at most ₹25,000. INR only, one company, GST as one rate. All are in `data/policy.json`.
- **Claude:** used through the local Claude Code command, so it runs on this laptop only. The draft messages use a smaller model, because a person reads them before sending.
- **Test data:** all made up.

# Interview checklist

- [ ] Laptop plugged in, online, notifications off
- [ ] Claude Code logged in: run `claude -p "Reply with OK"` the same morning
- [ ] Start the app: `.venv\Scripts\python -m uvicorn app:app --port 8000`
- [ ] The top bar says **AI on**
- [ ] One full rehearsal (inbox batch, S6 and S2 drafts, S4 confirm), then **All runs → Reset demo data**
- [ ] Hosted link open in a second tab, and woken up beforehand (the free tier sleeps)

# Submission reminders (send these yourself)

- [ ] Record the demo video, 5 minutes or less
- [ ] By November 6: send the live link and the video link to your coordinator
