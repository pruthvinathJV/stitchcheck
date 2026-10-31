# Submission-day checklist

Deadline: **Wednesday Nov 4, 6 PM PT.** Judges read the public GitHub repo, the README and a 3-minute video. All times are Pacific.

| When (PT) | Step |
|---|---|
| Wed Nov 4, before 5 PM | Last README edits merged to main; `scripts/release/check_repo.sh` passes |
| Wed Nov 4, 5 PM | Freeze: tag `v1.0-submission`, push, deploy from the tag, smoke test |
| Wed Nov 4, by 5:45 PM | Submission form sent (aim for 15 minutes of slack) |
| Wed Nov 4, 6 PM | Deadline. No commits to main until judging ends |
| Nov 4 or 5 | Post on X |
| Nov 5 to 8 | Live URL kept up with one warm container |

## 1. Before the freeze

- [ ] README placeholders filled where the work is done: demo video link, live app URL, phone test set results, tech-pack results, the Muse Code line. Any TODO left is deliberate and still reads honestly.
- [ ] Every number in the README traces to a file under `results/`.
- [ ] "Built during the hackathon" ticks match what is in the repo.
- [ ] On main, with a clean tree: `scripts/release/check_repo.sh` prints "All checks passed."

## 2. Freeze at 5 PM PT

- [ ] `git checkout main && git pull`
- [ ] `scripts/release/freeze.sh v1.0-submission`
  - refuses unless on main with a clean tree, reruns the repo check, creates the annotated tag, and prints the push and deploy commands; it never pushes or deploys itself
- [ ] Push, as printed: `git push origin main` then `git push origin v1.0-submission`
- [ ] On GitHub: the repo is public, the tag is listed, the README renders (architecture figure, tables).

## 3. Deploy from the tag

- [ ] `git checkout --detach v1.0-submission`
- [ ] `STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py`
- [ ] `git checkout main`
- [ ] If anything breaks later, redeploy from the same tag; never deploy from a working branch during judging.

## 4. Smoke test the live URL

- [ ] `python scripts/ops/smoke_e2e.py URL` passes.
- [ ] `GET URL/version` matches the tag (`v1.0-submission` and its commit).
- [ ] On a phone, logged out, in a private window: the app opens, "Demo data" badge visible, a gallery photo runs end to end, the report generates and its agent trace opens.
- [ ] Both AI modes answer (Muse Spark and Muse Spark Open), and the daily caps ($10 Muse Spark, $5 Muse Spark Open) are in place.

## 5. Submission form

- [ ] Public repo URL (open it in a private window first).
- [ ] 3-minute video link: at most 3:00, public or unlisted, plays without signing in.
- [ ] Team list.
- [ ] Live app URL in the README and, if the form has a field for it, in the form.
- [ ] Screenshot the confirmation page.

## 6. After the deadline

- [ ] **No commits to main after 6 PM PT Nov 4 until judging ends.** Fixes go on a branch (for example `post-deadline`) and are not merged or deployed until judging ends.
- [ ] Keep the live URL up through **Nov 8**: one warm container Nov 5 to 8 (the `STITCHCHECK_WARM=1` deploy from the tag). Check `/health` and the day's spend once a day.
- [ ] Post on X on Nov 4 or 5 (drafts in `docs/x_post.md`), with the video and repo links.
- [ ] Community vote: when voting opens, share the project page and the post, and send one reminder before it closes.
