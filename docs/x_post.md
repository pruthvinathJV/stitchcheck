# X post drafts

Placeholders: `[VIDEO]` the demo video link, `[REPO]` the public GitHub repo, `[TAG]` the hackathon's official hashtag if it has one (otherwise delete it). X counts every link as 23 characters; the counts below use that and the longest likely hashtag (20 characters); each post is under 280. At most one hashtag per post. Post on Nov 4 after the deadline or on Nov 5. Every number is from the README.

## Single post, draft 1

> A new 25-machine garment unit in rural Andhra Pradesh has to prove its quality before anyone gives it work. StitchCheck turns a phone into a stitch inspector: SAM 3.1 finds the seam, Muse Spark judges it, the checker decides. [TAG]
>
> [VIDEO]

## Single post, draft 2

> StitchCheck: Muse Spark inspects garment seams with a fine-tuned classifier as its tool. With the checker in the loop it caught 92.9% of major defects on fabrics it never trained on. Muse Spark alone missed about half.
>
> [VIDEO] [REPO]

## Thread (4 posts)

**1/4**

> Small garment units in India have to prove their quality to win orders. Paper checklists miss defects and give buyers no data. We built StitchCheck, a phone app for the checker on the line. [TAG]
>
> Demo: [VIDEO]

**2/4**

> The checker photographs a seam. SAM 3.1 finds it, a ConvNeXt-Tiny classifier scores tiles along it, and Muse Spark calls that classifier as a tool, compares the seam with a good one and explains its call. Disagreements go to the checker, who can correct by voice in Telugu.

**3/4**

> On held-out StitchingNet fabrics, with the checker in the loop: 92.9% of major defects caught, 9.5% of good seams flagged. Muse Spark Open mode: 94.4% and 20.2%. Muse Spark alone missed about half, and never named a defect type it had not seen.

**4/4**

> The buyer report's numbers are filled in by the server from logged data, and a validator rejects any number the model typed. Process first: it points at machines and operations, never at people. Code, results and limitations: [REPO]
