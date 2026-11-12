# HR data export

**All data in this folder is synthetic.** Every candidate, answer, name, email address and number was made up for this exercise. None of it describes a real person.

This is what our HR Manager exported for you. The export was taken on 20 September 2026, so nothing in it is dated later.

## Files

| File | What it contains |
|---|---|
| `questions.md` | The six interview questions every candidate answers, each with the 1-to-4 guide HR scores against |
| `answers/` | Recent candidate answers, submitted 14 to 20 September. Each `.json` file holds one answer. Most carry a transcript. Five are audio only, with the recording in the matching `.m4a` file |
| `scoring_sheet.csv` | Scores from August and September. Each answer was scored by two people in HR. The pass mark is in `questions.md` |
| `hr_reference_scores.csv` | HR's own scores for ten of the answers in `answers/`, using the grading guides in `questions.md` |
| `pipeline_log.csv` | Eight weeks of applications: who applied, when they were invited, when they submitted answers, and where they stand |
| `hr_time_log.csv` | How HR's working hours were spent each week, by task, over the same eight weeks |
| `inbox_export.csv` | Two weeks of the HR inbox, with the sender, subject and the start of each message |

The same `candidate_id` means the same person across `answers/`, `scoring_sheet.csv` and `pipeline_log.csv`. Candidates' email addresses in `inbox_export.csv` match `pipeline_log.csv`.

## Data dictionary for `pipeline_log.csv`

**`cv_tier`**, the result of the CV screen:

- **Tier 1:** the strongest CVs.
- **Tier 2:** middle.
- **Tier 3:** the weakest CVs. Most are not invited to answer questions.

**`outcome`**, where the candidate stood on 20 September:

- **`not_invited`:** not invited to answer interview questions after the CV screen. `outcome_on` is the date of that decision.
- **`no_answers_received`:** invited, but never submitted answers. `outcome_on` is the date HR closed the application.
- **`awaiting_decision`:** still in progress on the export date. Either invited and not yet submitted, or answers submitted and not yet decided. `outcome_on` is empty.
- **`advanced`:** moved on to the next hiring stage. `outcome_on` is the date of that decision.
- **`rejected`:** not taken forward after their answers were reviewed. `outcome_on` is the date of that decision.
- **`withdrew`:** the candidate withdrew from the process before HR made a decision. `outcome_on` is the date they told HR.

You may add your own data alongside these files.
