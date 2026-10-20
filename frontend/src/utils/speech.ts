export const speechPreferenceKey = "gpgest.read-aloud.preferences";
export const speechPreferenceEvent = "gpgest-read-aloud-preferences";
export const speechRates = [0.85, 0.95, 1.05];
export type SpeechPreferences = { voice: string; rate: number };

export function readSpeechPreferences(): SpeechPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(speechPreferenceKey) || "{}");
    return {
      voice: typeof value.voice === "string" ? value.voice : "",
      rate: speechRates.includes(value.rate) ? value.rate : 0.95,
    };
  } catch {
    return { voice: "", rate: 0.95 };
  }
}

export function voiceKey(voice: SpeechSynthesisVoice) {
  return `${voice.lang}:${voice.voiceURI || voice.name}`;
}

export function portugueseVoices(voices: SpeechSynthesisVoice[]) {
  function rank(voice: SpeechSynthesisVoice) {
    const lang = voice.lang.toLowerCase().replace("_", "-");
    return (
      (lang === "pt-pt" ? 1000 : 0) +
      (/natural|neural|enhanced|premium/i.test(voice.name) ? 100 : 0) +
      (voice.default ? 10 : 0) +
      (voice.localService ? 1 : 0)
    );
  }
  return voices
    .filter((voice) => voice.localService && /^pt(?:-|_|$)/i.test(voice.lang))
    .sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name, "pt-PT"));
}

export function speechParts(text: string) {
  const parts: { text: string; paragraphEnd: boolean }[] = [];
  for (const paragraph of text.split(/\n+/)) {
    const normalized = paragraph
      .replace(/\bERP\b/g, "é erre pê")
      .replace(/\bTPA\b/g, "tê pê á")
      .replace(/\bPOS\b/g, "ponto de venda")
      .replace(/\bMB\s+WAY\b/gi, "éme bê uei")
      .replace(/−/g, " menos ")
      .replace(/\+/g, " mais ")
      .replace(/=/g, " igual a ")
      .replace(/€/g, " euros ")
      .replace(/\s\/\s/g, " ou ")
      .replace(/\s+/g, " ")
      .trim();
    if (!normalized) continue;
    const sentences =
      typeof Intl.Segmenter === "function"
        ? Array.from(
            new Intl.Segmenter("pt-PT", { granularity: "sentence" }).segment(
              normalized,
            ),
            (part) => part.segment.trim(),
          )
        : normalized.split(/(?<=[.!?])\s+/);
    for (const sentence of sentences) {
      if (sentence.length <= 220) {
        parts.push({ text: sentence, paragraphEnd: false });
        continue;
      }
      // Prefer clause boundaries; split words only for a single very long clause.
      let chunk = "";
      for (const clause of sentence.split(/(?<=[,;:])\s+/)) {
        if (chunk && chunk.length + clause.length + 1 > 220) {
          parts.push({ text: chunk, paragraphEnd: false });
          chunk = "";
        }
        for (const word of clause.split(" ")) {
          if (chunk && chunk.length + word.length + 1 > 220) {
            parts.push({ text: chunk, paragraphEnd: false });
            chunk = "";
          }
          chunk += `${chunk ? " " : ""}${word}`;
        }
      }
      if (chunk) parts.push({ text: chunk, paragraphEnd: false });
    }
    if (parts.length) parts[parts.length - 1].paragraphEnd = true;
  }
  return parts;
}
