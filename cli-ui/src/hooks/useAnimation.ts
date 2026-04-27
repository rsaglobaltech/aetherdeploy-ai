import { useEffect, useState } from "react"
import { FRAME_INTERVAL_MS, MASCOT_FRAMES } from "../constants/mascot-frames.js"
import type { AgentState } from "../types.js"

// Only animate during active work — idle and success use a static first frame
// so Ink stops re-rendering the terminal every 500 ms, which would otherwise
// interrupt keyboard input and make the prompt feel unresponsive.
const ANIMATED: ReadonlySet<AgentState> = new Set(["analyzing", "planning", "deploying", "error"])

export function useAnimation(state: AgentState): string {
  const frames = MASCOT_FRAMES[state]
  const [frameIndex, setFrameIndex] = useState(0)

  useEffect(() => {
    setFrameIndex(0)
    if (!ANIMATED.has(state)) return
    const id = setInterval(() => {
      setFrameIndex(i => (i + 1) % frames.length)
    }, FRAME_INTERVAL_MS)
    return () => clearInterval(id)
  }, [state, frames.length])

  return frames[frameIndex]!
}
