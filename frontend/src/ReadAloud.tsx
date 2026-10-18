import { ActionButton } from "./components/InteractionUI";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Settings2, Square, Volume2 } from "lucide-react";
import {
  portugueseVoices,
  readSpeechPreferences,
  speechParts,
  speechPreferenceEvent,
  speechPreferenceKey,
  voiceKey,
  type SpeechPreferences,
} from "./utils/speech";
import "./ReadAloud.css";

let activeReader: { stop: () => void } | null = null;

export default function ReadAloud({
  children,
  active = true,
}: {
  children: ReactNode;
  active?: boolean;
}) {
  const content = useRef<HTMLDivElement>(null);
  const utterances = useRef<SpeechSynthesisUtterance[]>([]);
  const pause = useRef<number | null>(null);
  const owner = useRef({ stop: () => {} });
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [preferences, setPreferences] = useState(readSpeechPreferences);
  const supported = Boolean(
    window.speechSynthesis && window.SpeechSynthesisUtterance,
  );
  const stop = useCallback(() => {
    if (pause.current !== null) {
      window.clearTimeout(pause.current);
      pause.current = null;
    }
    if (!utterances.current.length) return;
    for (const utterance of utterances.current) {
      utterance.onend = null;
      utterance.onerror = null;
    }
    utterances.current = [];
    if (activeReader === owner.current) {
      activeReader = null;
      window.speechSynthesis.cancel();
    }
    setReading(false);
  }, []);
  owner.current.stop = stop;

  useEffect(() => {
    if (!supported) return;
    const synth = window.speechSynthesis;
    const updateVoices = () => {
      try {
        setVoices(Array.from(synth.getVoices()));
      } catch {
        setVoices([]);
      }
    };
    updateVoices();
    synth.addEventListener("voiceschanged", updateVoices);
    return () => synth.removeEventListener("voiceschanged", updateVoices);
  }, [supported]);
  useEffect(() => {
    const update = () => {
      stop();
      setPreferences(readSpeechPreferences());
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === speechPreferenceKey || event.key === null) update();
    };
    window.addEventListener(speechPreferenceEvent, update);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(speechPreferenceEvent, update);
      window.removeEventListener("storage", onStorage);
    };
  }, [stop]);
  useEffect(() => {
    if (!active) stop();
  }, [active, stop]);
  useEffect(() => {
    const details: HTMLDetailsElement[] = [];
    let parent = content.current?.parentElement;
    while (parent) {
      if (parent instanceof HTMLDetailsElement) details.push(parent);
      parent = parent.parentElement;
    }
    const onToggle = (event: Event) => {
      if (!(event.currentTarget as HTMLDetailsElement).open) stop();
    };
    for (const element of details) element.addEventListener("toggle", onToggle);
    window.addEventListener("pagehide", stop);
    return () => {
      for (const element of details)
        element.removeEventListener("toggle", onToggle);
      window.removeEventListener("pagehide", stop);
      stop();
    };
  }, [stop]);

  const availableVoices = portugueseVoices(voices);
  const selectedVoice = availableVoices.find(
    (voice) => voiceKey(voice) === preferences.voice,
  );
  const automaticVoice = availableVoices[0];
  function changePreferences(patch: Partial<SpeechPreferences>) {
    activeReader?.stop();
    const next = { ...preferences, ...patch };
    setPreferences(next);
    setError("");
    try {
      localStorage.setItem(speechPreferenceKey, JSON.stringify(next));
      window.dispatchEvent(new Event(speechPreferenceEvent));
    } catch {
      setError(
        "A escolha foi aplicada, mas não foi possível guardá-la neste navegador.",
      );
    }
  }

  function listen() {
    if (!supported || !active) return;
    if (reading) {
      stop();
      return;
    }
    const text = content.current?.innerText.trim();
    if (!text) return;
    activeReader?.stop();
    setError("");
    const synth = window.speechSynthesis;
    const fail = () => {
      stop();
      setError(
        "Não foi possível ouvir a informação. Tente novamente ou verifique a voz portuguesa nas definições do dispositivo.",
      );
    };
    try {
      synth.cancel();
      const choices = portugueseVoices(synth.getVoices());
      const voice =
        choices.find((item) => voiceKey(item) === preferences.voice) ||
        choices[0];
      const parts = speechParts(text);
      if (!parts.length) return;
      const queue: SpeechSynthesisUtterance[] = parts.map((part, index) => {
        const utterance = new SpeechSynthesisUtterance(part.text);
        utterance.lang = voice?.lang || "pt-PT";
        utterance.rate = preferences.rate;
        if (voice) utterance.voice = voice;
        utterance.onend = () => {
          if (utterances.current !== queue) return;
          if (index === queue.length - 1) {
            utterances.current = [];
            if (activeReader === owner.current) activeReader = null;
            setReading(false);
            return;
          }
          pause.current = window.setTimeout(
            () => {
              pause.current = null;
              if (utterances.current !== queue) return;
              try {
                synth.speak(queue[index + 1]);
              } catch {
                fail();
              }
            },
            part.paragraphEnd ? 320 : 100,
          );
        };
        utterance.onerror = (event) => {
          if (utterances.current !== queue) return;
          if (event.error === "interrupted" || event.error === "canceled")
            stop();
          else fail();
        };
        return utterance;
      });
      utterances.current = queue;
      activeReader = owner.current;
      setReading(true);
      synth.speak(queue[0]);
    } catch {
      fail();
    }
  }

  return (
    <div className="read-aloud" data-ignore-dirty>
      <div ref={content} className="read-aloud-text">
        {children}
      </div>
      <div className="read-aloud-actions">
        <ActionButton type="button" disabled={!supported} onClick={listen}>
          {reading ? (
            <Square size={16} aria-hidden="true" />
          ) : (
            <Volume2 size={16} aria-hidden="true" />
          )}
          {reading ? "Parar leitura" : "Ouvir informação"}
        </ActionButton>
        {supported && (
          <details className="read-aloud-settings">
            <summary>
              <Settings2 size={16} aria-hidden="true" /> Voz e velocidade
            </summary>
            <div className="read-aloud-options">
              <label>
                Voz da leitura
                <select
                  aria-label="Voz da leitura"
                  value={selectedVoice ? preferences.voice : ""}
                  onChange={(e) => changePreferences({ voice: e.target.value })}
                >
                  <option value="">
                    Automática
                    {automaticVoice
                      ? ` — ${automaticVoice.name}`
                      : " (português de Portugal)"}
                  </option>
                  {availableVoices.map((voice) => (
                    <option key={voiceKey(voice)} value={voiceKey(voice)}>
                      {voice.name} —{" "}
                      {voice.lang.toLowerCase().replace("_", "-") === "pt-pt"
                        ? "Portugal"
                        : voice.lang}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Velocidade da leitura
                <select
                  aria-label="Velocidade da leitura"
                  value={preferences.rate}
                  onChange={(e) =>
                    changePreferences({ rate: Number(e.target.value) })
                  }
                >
                  <option value={0.85}>Mais pausada</option>
                  <option value={0.95}>Normal</option>
                  <option value={1.05}>Mais rápida</option>
                </select>
              </label>
              <p className="footnote">
                A escolha fica guardada neste navegador. Depois de alterar,
                carregue em «Ouvir informação».
              </p>
            </div>
          </details>
        )}
      </div>
      {(!supported || error) && (
        <p className="read-aloud-status" role="status">
          {supported
            ? error
            : "Este navegador não permite leitura em voz alta."}
        </p>
      )}
    </div>
  );
}
