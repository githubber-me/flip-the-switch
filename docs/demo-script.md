# Demo script (about 3 minutes)

Narrate decisions, not features. Use a big chat font and speed ramps. Dictate the voiceover with Wispr Flow.

| Time | Picture | Say |
|---|---|---|
| 0:00 | The Ramayana trailer, final file | "I made this trailer this week. 42 video prompts, 28 hours start to finish, and every prompt had to restate the look from scratch, because each model call starts cold." |
| 0:15 | The director alone, many tools | "A regional creator is the writer, artist, editor and producer. The tools don't share a memory. So I gave them a room." |
| 0:25 | Brief, script, critic objects, I rule | "My critic attacks the script before any money moves. I settle it." |
| 0:50 | Stills arrive in the thread | "Stills cost ten cents. Clips cost sixty-seven. So I review here." |
| 1:00 | **THE VETO:** I reject a still with a real reason, the agent fixes only that | Say the real reason. Do not stage one: use something actually wrong with a frame. |
| 1:15 | Producer quotes, I approve, the clip permission card appears, I approve | "Nothing paid happens until I answer in the room." |
| 1:35 | Clips arrive, final cut posted | |
| 1:45 | Room instructions in Console, `@producer show the log`, the diagram | "The brief is in the room. My rulings are in the room. Take Switch away, and the crew has no way to hand off, ask me, or remember what I said." |
| 2:15 | `!reset-all-agents`, then episode 2 brief, storyboard applies my earlier rejection | "I just wiped every agent's memory. It still remembers my ruling, because the ruling lives in the room." |
| 2:40 | The reel plays, repo URL | "It runs without credits in dry mode." |

## Keep it honest
- The trailer was not made in the room. The opening says so by contrast; never imply otherwise.
- Do not call the trailer "solo prompting": Claude Code made its Gulab calls and Codex made its stills while I directed. The real problem to show is that nothing carried over between calls.
- Measured trailer numbers: 42 video prompts (40 paid, 2 cancelled), about 28 hours elapsed, about 7.5 to 9 hours of active session including the Kannada dub and YouTube metadata, $22.72 on Gulab.
- Say "room instructions" and "room document". These are Switch's names. Do not say Rules, Tasks or Decisions panels unless the installed app really shows them.
- Only keep the episode 2 beat if the reset really works on camera. If it fails, cut it. Episode 2 stops at one still: the cap is $5.
- The "cheap stills" numbers are Gulab's listed prices: $0.096 per Nano Banana 2.1 still, $0.67 for a 5-second Kling O3 clip.

## Before submitting
- [ ] Attach the Wispr Insights scorecard (only prompts really dictated with Wispr count)
- [ ] README setup steps tested from a fresh clone in dry mode
- [ ] `studio.json` back to `"mode": "dry"` and `"gulab_board": null` in the commit
- [ ] No secrets in the repo
- [ ] Repo is public and the video link opens signed out
