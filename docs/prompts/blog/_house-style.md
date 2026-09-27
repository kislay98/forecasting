# Shared house style (already embedded in each prompt, kept here for editing in one place)

## Voice

Write as one engineer telling another what happened. Past tense, first person singular,
specific dates and numbers. The reader is technical but not a statistician: they know what
a p-value is and have never heard of Christoffersen.

Ban list. These are what make prose read as machine-made:

- "delve", "moreover", "furthermore", "it is worth noting", "that said", "at its core",
  "in today's world", "game-changer", "leverage" as a verb, "robust" as filler, "seamless",
  "crucial" more than once, "the key insight is", "let's dive in", "in this post we will".
- A roadmap paragraph after the intro that lists what the post will cover.
- A conclusion that restates the post. End on the strongest concrete fact, or on the
  question the work left open.
- Three-item lists everywhere. Vary sentence length hard: a nine-word sentence next to a
  thirty-word one.
- Hedging every claim. State the number, then caveat it once, then move on.
- Em dashes. Not one, anywhere. Rewrite the sentence instead. En dashes in date ranges are
  fine.

Do write:

- Short paragraphs, two to four sentences, like the reference posts.
- Section headers that are plain or a question: "Where did the leak come from?" beats
  "Understanding Data Leakage in Time Series Validation".
- Concrete artifacts inline: a YAML fragment, a file key, a four-row p-value table.
- The moments you were wrong, in full. A registered prediction that missed is the most
  human paragraph available and the most persuasive.

## Figures

Reference: the Hevo engineering posts on Medium ("Building a lossless Kafka Producer",
"Where's that event?"). Their diagrams are plain boxes and arrows, near monochrome,
numbered callouts 1 to n with a legend column that explains each number in one line, and a
grey caption underneath in sentence case. Copy that discipline, not their subject.

- Every figure explains a mechanism or a formula. No decoration, no stock imagery.
- A formula appears as a picture at least once per post: the recursion as a feedback loop,
  the score as a point on a number line, the correction as an interval moving.
- Inline SVG in an Astro component, one component per figure, in the site's existing
  figures folder. Follow whatever the existing components do.
- Must read in light and dark. The site has a dark-mode accent token for figures; use it
  rather than a hard-coded near-white, which was a real bug once.
- Legible at 320px wide. No horizontal scroll.
- One caption per figure, under it, in sentence case.

## Length and shape

1,200 to 1,800 words, which reads as five to seven minutes. Hard cap 2,000. Three to five
figures. One opening image is optional and never load-bearing.

## Honesty

Every number comes from the repository. If a number cannot be found, say so in the handback
note instead of inventing a plausible one. Do not round a p-value of 0.0056 to "about
0.005" when the threshold it failed against is 0.0064: the margin is the story.
