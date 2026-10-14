// Prefer distinct, moderately dark colours suitable for the existing rubric badges.
const palette = ["#245fc5", "#7c3fa0", "#167d86", "#a65d24", "#a13e68", "#50772e", "#5653a6", "#a24636", "#316f98", "#86632b", "#4d7967", "#78556a"];

function rgb(hex: string): number[] {
  return [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16));
}

export function suggestRubricColor(colors: (string | undefined)[]): string {
  const used = new Set(colors.map(color => (color || "#245fc5").toLowerCase()));
  if (!used.size) return palette[0];
  let candidates = palette.filter(color => !used.has(color));
  if (!candidates.length) {
    // Extend the palette when all the initial colours are in use.
    for (let index = 0; index < used.size + 128; index++) {
      const value = (index * 104729) % (144 ** 3);
      const color = "#" + [32 + value % 144, 32 + Math.floor(value / 144) % 144, 32 + Math.floor(value / (144 ** 2)) % 144]
        .map(channel => channel.toString(16).padStart(2, "0")).join("");
      if (!used.has(color)) candidates.push(color);
    }
  }
  const existing = [...used].filter(color => /^#[0-9a-f]{6}$/.test(color)).map(rgb);
  const distance = (color: string) => {
    const values = rgb(color);
    return Math.min(...existing.map(other => values.reduce((sum, value, i) => sum + (value - other[i]) ** 2, 0)));
  };
  return candidates.reduce((best, color) => distance(color) > distance(best) ? color : best, candidates[0] || "#245fc5");
}
