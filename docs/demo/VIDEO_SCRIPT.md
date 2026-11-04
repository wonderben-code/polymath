# Polymath demo film: narration and shot list

Three minutes, built from the live screen recordings of 2 November 2026 (see `README.md` in this folder). Two cuts exist from the same shot list and the same narration: **A, the best-bits cut**, which jumps to the strongest moment of each feature and holds on the result; and **B, the unfold cut**, where each feature runs continuously from the first keystroke to the result with speed ramps instead of jumps, so the circle, the map and the stages are seen forming while the narrator speaks. Both cuts run about 2 minutes 56 seconds; cut B carries the voiceover. The time column gives each block's start and end in cut B.

Read at a natural pace (about 110 to 130 words a minute). Each block fits its segment with a little room; a short silence at the end of a block is fine.

| Cut B | On screen | Narration (final, 4 Nov) |
|---|---|---|
| 0:00–0:16 | Title card: *Polymath. Discoveries hide between fields of science. Polymath finds them.* Then the home page. | Science has thousands of fields. Answers to one problem often hide in unrelated fields. Polymath finds them. Ask anything, and Meta's Muse Spark searches all of open science. |
| 0:16–0:38 | **Ask.** The question is typed, the run unfolds, then the answer: numbered claims with their labels and source passages, and "what remains unanswered". | It has five tools, for any question, problem or invention. First, Ask. Muse Spark reads real papers and answers with numbered claims, each showing the exact passage it came from, and how certain it is. |
| 0:38–0:59 | **Solve.** The problem is typed, the run unfolds, then the lock, the fits from other fields, and "A new key was forged". | Second, Solve. Give it any problem you can imagine. It checks whether science already has the answer. If it doesn't, it invents a new solution, borrowing ideas from other fields and combining them into something new, and tells you honestly where it's still weak. |
| 0:59–1:31 | **Converge.** The theme is typed; the empty circle of twelve fields; connections form; the synthesis glows in the centre; "Paused at your spending ceiling". | Third, Converge, for exploring. Give it any topic you're curious about. Muse Spark picks twelve far-apart fields and asks what each one knows about it. It checks every link against real research, and throws out any that don't hold up. What survives comes together as a new idea, or a new combination of ideas, that no single field could see. |
| 1:31–1:56 | **Frontier.** The question is typed; the map grows from one node to gaps and a six-step plan; the programme is paused. | Fourth, Frontier. Give it any unsolved problem, however sci-fi. It maps the fields closest to it, works out the gaps between today and that goal, hunts other sciences for answers, and breaks the hardest gaps into steps. Here, for example, a six-step plan for a space elevator, created in just nineteen minutes. |
| 1:56–2:44 | **Studio.** The whale brief and its requirements; stages light up; the idea map; the concept art appears; engineering cutaways; the stress test; "Still to invent"; "Take it further"; a glimpse of the Dyson swarm. | Fifth, Studio. It invents anything you can describe or dream of. Give it a brief. Muse Spark searches the literature for mechanisms that already work. Here, for example, a humpback's fins, an owl's feathers and a seal's swimming stroke are combined into a new submarine that glides in a sawtooth. Meta's image model paints the concept art. Then it engineers the design with real numbers, tests it against your requirements, and lists what still has to be invented. Every source is real, and it says what it cannot prove. If you can dream it, Studio can start inventing it. |
| 2:44–2:56 | End card: *askpolymath.com · Meta Muse Spark × Polymath · reads arXiv and OpenAlex · publishes to Zenodo · free to try* | Any question, problem or invention. Try it free at askpolymath.com, and publish anything you discover to Zenodo. Polymath: discoveries across all of science, powered by Meta. |

The narration is the owner's final text of 4 November, generated as seven ElevenLabs clips (the title card and home page share one clip). The earlier draft narration is in the file's history.

## Shot list (source timecodes)

Times are seconds into each full recording (`film/src/*.mp4` are seekable copies of the raw WebM files).

| Segment | Source | Range | Speed (A) | Speed (B) | Notes |
|---|---|---|---|---|---|
| Home | solve | 1–9 | 1× | 1× | Hero and the mode switch |
| Ask type | ask | 6.5–10.5 | 1× | 1× | Question typed, "Investigate question" |
| Ask run | ask | 10.5–114 | 25× | 25× | Steps count up in the record |
| Ask result | ask | 118–146 | 2× | 2× | Claims, labels, sources, what remains |
| Solve type | solve | 7–11 | 1× | 1× | |
| Solve run | solve | 11–684 | 90× | 90× | Finder, three fits, forge |
| Solve result | solve | 686–700 | 1.5× | 1.5× | The lock, the forged key, the fits |
| Converge type | converge | 3–10 | 1.5× | 1.5× | Theme typed, "Begin converging" |
| Converge unfold | converge | 10–700 | 40× | 40× | Circle fills, synthesis glows |
| Converge finish | converge | 700–710 | 1× | 1× | "Paused at your spending ceiling", tally |
| Frontier type | frontier | 4–10 | 1.5× | 1.5× | |
| Frontier unfold | frontier | 10–1150 | 80× | 80× | Map grows |
| Frontier result | frontier | 1150–1157 | 1× | 1× | Six-step plan, Pause |
| Studio brief | whale | 4–12 | 1.5× | 1.5× | Starter, requirements, "Invent it" |
| Studio stages | whale | 12–860 | 70× | 70× | Stages light up, idea map grows |
| Studio art | whale | 860–882 | 2× | 2× | "Painting concept art", the picture appears |
| Studio sheet | whale | 2437–2482 | 2.5× | 3× | Concept art and gallery, inspirations, engineering cutaways, stress test, still to invent, sources, take it further |
| Dyson glimpse | dyson | 1118–1122 | 1× | 1× | Engineering drawings of the swarm |

Cut A holds a still of the finished map and of the forged key for a beat and drops the typing in Converge and Frontier; cut B keeps every feature continuous.

## The voiceover

1. The narration is generated as seven clips (blocks 1+2 together, then 3 to 8) with ElevenLabs, a calm mid-pace voice at normal speed.
2. The clips are laid over cut B at each block's start; silence fills the rest of the block. A clip longer than its block lengthens that block (a card holds longer; a feature's sped-up unfold runs a little slower), never a cut.
3. `scripts/demo/build.py B --voice <dir>` renders `polymath-demo-B-voiced.mp4` from the clips in that folder (named 1 to 7, any audio format).
