export type AgentState = "idle" | "analyzing" | "planning" | "deploying" | "error" | "success"

// Each element is a complete animation frame (lines joined with \n).
// Rules: all frames must have consistent indentation (leading space before ╭),
// emojis are only placed on line 5 (never on the ╰─────╯ line) to avoid
// terminal column-width misalignment with wide emoji characters.
export const MASCOT_FRAMES: Record<AgentState, string[]> = {
  // Idle — calm robot, gentle blink cycle
  // ☁ (U+2601) is ambiguous-width so we use ASCII ~ instead
  idle: [
    " ╭─────╮\n │ ◉ ◉ │\n │  ω  │\n ╰─────╯\n  ~   ~  ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ω  │\n ╰─────╯\n  ~   ~  ",
    " ╭─────╮\n │ ─ ─ │\n │  ω  │\n ╰─────╯\n  ~   ~  ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ω  │\n ╰─────╯\n  ~   ~  ",
  ],
  // Analyzing — eyes dart left/right, scanning 🔍
  analyzing: [
    " ╭─────╮\n │ ◎ · │\n │  ─  │\n ╰─────╯\n 🔍      ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ─  │\n ╰─────╯\n  🔍     ",
    " ╭─────╮\n │ · ◎ │\n │  ─  │\n ╰─────╯\n 🔍      ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ─  │\n ╰─────╯\n  🔍     ",
  ],
  // Planning — one brow raised, deep in thought 💭⚙
  planning: [
    " ╭─────╮\n │ ¬ ◉ │\n │  ▾  │\n ╰─────╯\n 💭  ⚙  ",
    " ╭─────╮\n │ ◉ ¬ │\n │  ▴  │\n ╰─────╯\n 💭   ⚙ ",
    " ╭─────╮\n │ ¬ ¬ │\n │  ▾  │\n ╰─────╯\n 💭 ⚙⚙  ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ▴  │\n ╰─────╯\n 💭  ⚙  ",
  ],
  // Deploying — wide excited eyes, rocket lifts off 🚀
  deploying: [
    " ╭─────╮\n │ ★ ★ │\n │  ▽  │\n ╰─────╯\n 🚀      ",
    " ╭─────╮\n │ ★ ★ │\n │  ▽  │\n ╰─────╯\n  🚀     ",
    " ╭─────╮\n │ ✦ ✦ │\n │  ▽  │\n ╰─────╯\n   🚀    ",
    " ╭─────╮\n │ ★ ★ │\n │  ▽  │\n ╰─────╯\n  🚀     ",
  ],
  // Error — X eyes, alarmed 💥
  error: [
    " ╭─────╮\n │ × × │\n │  ∧  │\n ╰─────╯\n  💥     ",
    " ╭─────╮\n │ ✕ ✕ │\n │  >  │\n ╰─────╯\n   💥    ",
    " ╭─────╮\n │ × × │\n │  ∧  │\n ╰─────╯\n  💥     ",
    " ╭─────╮\n │ ✕ ✕ │\n │  >  │\n ╰─────╯\n   💥    ",
  ],
  // Success — happy eyes, sparkles ✨
  success: [
    " ╭─────╮\n │ ◉ ◉ │\n │  ∪  │\n ╰─────╯\n ✨   ✨ ",
    " ╭─────╮\n │ ★ ★ │\n │  ∪  │\n ╰─────╯\n ✨  🎉 ",
    " ╭─────╮\n │ ◉ ◉ │\n │  ∪  │\n ╰─────╯\n ✨   ✨ ",
    " ╭─────╮\n │ ★ ★ │\n │  ∪  │\n ╰─────╯\n ✨  🎉 ",
  ],
}

export const FRAME_INTERVAL_MS = 500
