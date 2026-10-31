# Demo video script (hard cap 3:00)

Plain English narration at about 150 words a minute (about 390 words in total, no segment faster than about 145 words a minute). Every number spoken or shown comes from the README. Screens from the hosted demo show the "Demo data" badge; numbers on demo screens (DHU, counts, alert text) are demo data and are not narrated as results.

Optional Telugu lines are marked **[Telugu, optional]**. They are drafts: check the wording before recording and show English subtitles.

| Time | Shot (on screen) | Narration |
|---|---|---|
| 0:00 to 0:20 | The unit in Kadiri: rows of machines, a tailor sewing a shirt collar. Title card: "StitchCheck". | This is a new garment unit in Kadiri, Andhra Pradesh. Twenty-five machines, skilled tailors, and no track record. Before any buyer gives it work, it has to prove its quality. Today that proof is a paper checklist. It misses defects, and the buyer cannot check any of it. |
| 0:20 to 0:45 | The supervisor photographs a tech pack (TP-01, the fictional "Sample Buyer Co." sheet). The app shows the draft inspection plan: critical seams, stitch targets, AQL levels, severity rules. The supervisor taps Confirm. | The day starts with the buyer's tech pack. The supervisor takes one photo, and Muse Spark drafts the inspection plan: critical seams, stitch targets, AQL levels and the buyer's own severity rules, in any language. Code checks every value, and nothing is used until the supervisor confirms it. |
| 0:45 to 1:05 | The checker photographs a collar seam. Step 1 card: the crop and "Classifier: Skipped stitch". Tap: the photo with the seam tinted green. "Reviewing..." then Muse Spark's label, severity and one-line reason. | Now the checker photographs a seam. SAM 3.1 finds the seam, shown in green. Our classifier, fine-tuned on stitch images, flags it in a fraction of a second. Muse Spark then calls the classifier as a tool, compares the seam with a good reference, and explains its call. |
| 1:05 to 1:20 | A second photo with the "Your call" badge. The checker holds the mic button and speaks. The voice sheet shows the transcript and the parsed label; the checker taps Confirm. | When the AI and the classifier disagree, the photo comes to the checker. Here the checker corrects one call, in Telugu. Muse Spark maps the words onto the fixed lists, and one tap confirms it. |
| | **[Telugu, optional]** The checker speaks: "ఇది వంకర కుట్టు కాదు, కుట్లు జారాయి." Subtitle: "This is not a crooked seam, it is a skipped stitch." | |
| 1:20 to 1:30 | Expand "How the AI decided" once: `classify_stitch`, its top-2 labels with probabilities, then the final answer. | Every decision keeps its trace: the tool call, what it returned, and the answer. |
| 1:30 to 2:00 | Dashboard: DHU and defective % on top, bars by machine and by operation. A trend alert banner for one machine; tap it to open the mechanic's work order in Telugu and English. Then the audit card with the pass chance. | Every confirmed defect lands on the dashboard, by machine and by operation, never by person. When one machine keeps repeating a defect, a trend alert fires, and Muse Spark drafts a work order for the mechanic: needle, tension, timing, in Telugu and English. The supervisor also sees the chance the lot would pass a buyer's AQL audit at today's defect rate. |
| 2:00 to 2:15 | Report screen: "Generate today's report", the buyer report in English with supervisor actions in Telugu and English; scroll to the trace with `get_dhu`, `get_top_issues`, `get_audit_pass_chance`. | At the end of the shift, Muse Spark writes the buyer report from tool results only. The server fills in every number from logged data, and a validator rejects any number the model typed. |
| 2:15 to 2:30 | Start day screen: the two AI mode cards with their figures. Tap "High sensitivity (Muse Spark Open)". | One tap switches to Muse Spark Open. On the test set it catches 94.4% of major defects instead of 92.9%, but flags 20.2% of good seams instead of 9.5%. It is also the automatic fallback. |
| 2:30 to 2:50 | The README results table, StitchingNet test. Highlight the StitchCheck row (92.9%), then the Muse Spark zero-shot and good-reference rows. Then a line: "Muse Spark never flagged unknown defect". | Now the honest part. On fabrics it never trained on, StitchCheck with the checker catches 92.9% of major defects. Muse Spark alone misses about half, and never named a defect type it had not seen. So the classifier is its tool, and the checker decides. |
| 2:50 to 3:00 | "Built during the hackathon" list from the README, then an end card: the Meta models in one line, the repo link. | Built during the hackathon with Muse Spark, Muse Spark Open, SAM 3.1 and Muse Voice Transcribe. StitchCheck: proof of quality, from a phone. |

## If the phone test set is scored before recording

At 2:30, replace "On fabrics it never trained on, StitchCheck with the checker catches 92.9% of major defects" with the phone-set headline exactly as the README states it, and keep the StitchingNet figure on screen. Do not change any other line.

## Recording notes

- Keep the total at or under 3:00. Cut shots before cutting the 2:30 honest-results segment.
- Show "Demo data" on every hosted-demo screen. Never show a ranking of workers or a per-operator view.
- Captions on throughout. Numbers as digits on screen, matching the README.
- End card text: "StitchCheck. Muse Spark, Muse Spark Open, SAM 3.1, Muse Voice Transcribe. [repo link]".
