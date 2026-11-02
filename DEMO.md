# Five-minute walkthrough

Everything here was run in the app. Say it in your own words; these are prompts, not a script.

**Before recording:** restart the app (so the Claude call is live, not cached), open it in a fresh or private browser window, and use the laptop version so scans use Claude.

| Time | Do | Say |
|---|---|---|
| 0:00 | New invoice screen | An AP clerk gets hundreds of PDF invoices a month, opens each one, finds the PO in a spreadsheet and checks the numbers. It's slow, and a tired person on a Friday makes expensive mistakes. So: rules decide, AI only reads scans, and nothing is approved unless every check actually ran and passed. |
| 0:30 | Run S1 | Point at the stages as they tick. It's 65 dollars over the PO, inside the 248 tolerance, so it's approved and the variance is recorded. Click "Fields read" and show the highlights on the page. |
| 1:15 | Run S1 again | Same invoice twice. Same number, amount and date, already approved: rejected. That's certain, so there's nothing for a person to weigh. |
| 1:35 | Run S2 | This invoice looks fine by itself, 8,100 against a 40,000 PO. But two earlier invoices already used 32,500, so it takes the PO to 40,600 against a 40,250 limit. Show the sums at stage 8 and the next step, with the exact figures to ask for. The tolerance applies once per PO, so a vendor can't collect it on every split. |
| 2:25 | Run S3 | A different invoice number, and it passes the PO match, so matching alone would pay this twice. The duplicate stage catches it: same amount, same PO, related number, 7 days apart. It's held, not rejected, because it might be a real second order. Show the earlier invoice beside it. |
| 3:10 | Run S4 | A low-quality scan with no text in it. Claude reads it, about 15 to 25 seconds. It's still a hold, because a misread must not move money. Check the form against the page, press Re-run: 11 of 11 checks, approved, and the record shows the first hold. |
| 4:20 | Dashboard | Every run, its status and output. Filter by holds, reopen one. |
| 4:40 | Limits | It's been tested on three invoice layouts I made. An unfamiliar layout usually holds, but a wrong reading could occasionally get through on a text PDF, so I'd audit a sample before trusting auto-approval on a real inbox. The tolerance numbers are my assumption; a real company sets its own. The hosted link has no AI, so scans there go straight to a blank form. |

If Claude fails live, say so: a failed read gets a blank form and the same hold, so the process carries on.

**Keep for questions:**
- **"Can a clerk just type the right number?"** Run S6, click "This reading is wrong" on Bank last 4, type 4471, re-run. It holds with `H-OVERRIDE`, and only a final decision with a note gets it through.
- **Embedded tax and bundled lines:** S7 works out net from the printed 8%, and approves with a note that lines weren't checked.
- **Implied PO:** S8 suggests PO-4950 but never assumes it. Enter it and it approves.
- **Missing information:** S5 has no invoice number, so it holds and asks for it.

## Why the choices are what they are

- **Why rules and not AI for the decision?** A rule gives the same answer every time and can be explained line by line. AI is only used where no rule can work, reading an image, and a person checks that reading.
- **Why does REJECT fire so rarely?** A wrong hold costs a minute. A wrong reject damages a vendor relationship. So reject is only for certain cases: blocked vendor, cancelled PO, exact duplicate.
- **Why no confidence score?** Nobody could defend a threshold like 87%. Each field is found, missing or conflicting, which a clerk can check against the page.

## Limits and assumptions to be upfront about

- **Field reading:** tested on three layouts built for this demo. A wrong value on a text PDF could survive the cross-checks; a person doesn't check text PDFs first.
- **Two-way match only:** invoice against PO. There's no goods-receipt data, so it can't confirm the goods arrived.
- **Scans:** a clerk who mistypes a value on a scan could get through. A real deployment would add a second approver.
- **Duplicates:** a resend with an unrelated number, over 14 days apart, on a different PO won't be caught. A statement reconciliation would catch it. Equal monthly fees on one PO hold every month, a false alarm accepted on purpose.
- **Bank check:** compares the last 4 digits only, and only when bank words are printed.
- **Assumptions:** 2% or 250 tolerance, USD only with no conversion, a bare `$` means USD, one company, PDF upload standing in for the mailbox.
- **Claude scan reading:** 3 of 3 correct on the one sample scan, plus one more through the app. That shows it works on that scan, not how accurate it is in general.
- **Cut from the design to fit the time:** line-item checks, fuzzy vendor name suggestions, detecting several invoices in one PDF. Also no email intake, logins or payments.
- **Test data:** all made up.

# Interview checklist

- [ ] Laptop plugged in, online, notifications off
- [ ] Claude Code logged in: run `claude -p "Reply with OK"` the same morning
- [ ] App restarted: `.venv\Scripts\python -m uvicorn app:app --port 8000`
- [ ] Badge at the top right says **AI reader: on**
- [ ] Private browser window (or press **Reset demo data** on the Dashboard)
- [ ] One rehearsal, then Reset again. S1 must come back as APPROVE
- [ ] Hosted link open in a second tab, and woken up beforehand (the free tier sleeps)
- [ ] Order ready: S1, S1 again, S2, S3, S4, then S6 for the override question

# Submission reminders (send these yourself)

- [ ] Day 1 email to your coordinator: `ASA Case Study — [Your Name] — PS-1`
- [ ] Record the demo video, 5 minutes or less
- [ ] By November 6: send the live link and the video link
