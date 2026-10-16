"""Browser checks with fictitious data and a simulated speech engine; no API writes."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
output = root / ".local/cash-help-audio-ui"
output.mkdir(parents=True, exist_ok=True)
methods = {"numerario": "Numerário", "cartao": "Multibanco / TPA", "mbway": "MB WAY", "transferencia": "Transferência", "outro": "Outros meios"}
boot = {
    "mode": "demonstracao", "company": {"id": "audio-test", "name": "Empresa de testes"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "payment_methods": methods,
    "user": {"id": "ui", "name": "Utilizador de testes", "role": "administrador"},
    "stores": [{"id": "s1", "name": "Loja principal"}],
    "registers": [{"id": "r1", "store_id": "s1", "name": "Caixa 01"}],
    "accounts": [], "categories": [], "users": [{"id": "ui", "name": "Utilizador de testes"}],
}
session = {
    "id": "cs1", "register_id": "r1", "operator_id": "ui", "opened_at": "2026-10-15T10:00:00Z", "closed_at": None,
    "balance_cents": 5000, "opening_cents": 4000, "count_data": {},
    "sheet": {
        "workflow_mode": "local", "day": "2026-10-15",
        "rows": [{"key": key, "count": 0, "expected_cents": 4000 if key == "numerario" else 0, "credits_cents": 0, "debits_cents": 0} for key in methods],
        "reconciliation": {
            "registered_total_cents": 0, "checked": False, "realized": {},
            "rows": [{"key": key, "name": name, "declared_cents": 0, "registered_cents": 0, "manual_cents": 0, "erp_cents": 0, "sources": []} for key, name in methods.items()],
        },
    },
}
speech_mock = """
const state = {queue: [], spoken: [], cancels: 0, voices: [
  {name: 'English', lang: 'en-US', localService: true},
  {name: 'Brasil Natural', lang: 'pt-BR', localService: true},
  {name: 'Portugal Online (Natural)', lang: 'pt-PT', localService: false},
  {name: 'Portugal local', lang: 'pt-PT', localService: true},
]};
window.testSpeech = state;
Object.defineProperty(window, 'SpeechSynthesisUtterance', {
  configurable: true, value: class {constructor(text) {this.text = text;}}
});
Object.defineProperty(window, 'speechSynthesis', {
  configurable: true, value: Object.assign(new EventTarget(), {
    getVoices: () => state.voices,
    cancel() {state.cancels++; state.queue = [];},
    speak(utterance) {
      state.queue.push(utterance);
      state.spoken.push({text: utterance.text, lang: utterance.lang, voice: utterance.voice?.name, rate: utterance.rate});
    }
  })
});
"""


def mock_api(route):
    assert route.request.method == "GET", "Financial mutations are forbidden"
    path = urlsplit(route.request.url).path
    data = boot if path == "/api/bootstrap" else [session] if path == "/api/sessions" else {} if path == "/api/dashboard" else []
    route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


def enter_cash(page):
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Folha de caixa", exact=True).click()
    page.get_by_text("Conferência e fecho", exact=True).click()


def finish_speech(page):
    page.evaluate("""async () => {
      for (let count = 0; testSpeech.queue.length && count < 100; count++) {
        testSpeech.queue.shift().onend({});
        await new Promise(resolve => setTimeout(resolve, 360));
      }
    }""")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="pt-PT")
    page.add_init_script(speech_mock)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)
    enter_cash(page)
    info = page.get_by_role("button", name="Informação sobre a conferência do faturado", exact=True)
    help_text = page.locator(".local-cash-conference-help")
    info.click()
    assert page.evaluate("testSpeech.spoken.length") == 0
    listen = help_text.get_by_role("button", name="Ouvir informação", exact=True)
    listen.click()
    expect(help_text.get_by_role("button", name="Parar leitura", exact=True)).to_be_visible()
    finish_speech(page)
    expect(listen).to_be_visible()
    spoken = page.evaluate("testSpeech.spoken")
    assert all(item["lang"] == "pt-PT" and item["voice"] == "Portugal Online (Natural)" and item["rate"] == 0.95 for item in spoken)
    assert all(len(item["text"]) <= 220 and item["text"][-1] in ".,;:!?" for item in spoken)
    text = " ".join(item["text"] for item in spoken)
    assert "Sem movimento confirma" in text and "Guardar estes valores não cria movimentos." in text
    assert "menos esperado" in text and "Ouvir informação" not in text
    pronunciation = page.evaluate("""async (text) => {
      const {speechParts} = await import('/src/utils/speech.ts');
      return speechParts(text).map(part => part.text).join(' ');
    }""", "MB WAY e TPA. O POS comunica com o ERP.\nDiferença = contado / conferido − esperado.")
    assert "éme bê uei e tê pê á" in pronunciation
    assert "ponto de venda comunica com o é erre pê" in pronunciation
    assert "Diferença igual a contado ou conferido menos esperado." in pronunciation
    listen.click()
    help_text.screenshot(path=str(output / "conference-desktop.png"))
    info.click()
    expect(help_text).to_be_hidden()
    assert page.evaluate("testSpeech.queue.length") == 0

    # Voice and speed choices persist and are shared across information panels.
    info.click()
    help_text.get_by_text("Voz e velocidade", exact=True).click()
    voice_select = help_text.get_by_label("Voz da leitura", exact=True)
    speed_select = help_text.get_by_label("Velocidade da leitura", exact=True)
    voice_select.select_option(label="Portugal local — Portugal")
    speed_select.select_option("0.85")
    listen.click()
    assert page.evaluate("testSpeech.spoken.at(-1).voice") == "Portugal local"
    assert page.evaluate("testSpeech.spoken.at(-1).rate") == 0.85
    help_text.get_by_role("button", name="Parar leitura", exact=True).click()
    enter_cash(page)
    info.click()
    help_text.get_by_text("Voz e velocidade", exact=True).click()
    expect(voice_select).to_have_value("pt-PT:Portugal local")
    expect(speed_select).to_have_value("0.85")
    help_text.screenshot(path=str(output / "voice-settings-desktop.png"))
    voice_select.select_option("")
    speed_select.select_option("0.95")
    help_text.get_by_text("Voz e velocidade", exact=True).click()
    expect(listen).to_be_visible()
    listen.click()
    help_text.get_by_role("button", name="Parar leitura", exact=True).click()
    expect(listen).to_be_visible()
    assert page.evaluate("testSpeech.queue.length") == 0

    # Starting another information panel stops the previous reader.
    listen.click()
    page_info = page.get_by_label("Informação sobre a folha de caixa", exact=True)
    page_info.click()
    page_help = page.locator(".cash-page-info-content")
    page_help.get_by_role("button", name="Ouvir informação", exact=True).click()
    expect(listen).to_be_visible()
    expect(page_help.get_by_role("button", name="Parar leitura", exact=True)).to_be_visible()
    page_info.click()
    expect(page_help).to_be_hidden()
    assert page.evaluate("testSpeech.queue.length") == 0

    # Completion and errors restore usable controls without changing any values.
    listen.click()
    finish_speech(page)
    expect(listen).to_be_visible()
    listen.click()
    page.evaluate("testSpeech.queue[0].onerror({error: 'language-unavailable'})")
    expect(help_text.get_by_role("status")).to_contain_text("Não foi possível ouvir")
    expect(listen).to_be_visible()
    listen.click()
    expect(help_text.get_by_role("status")).to_have_count(0)
    page.get_by_text("Conferência e fecho", exact=True).click()
    expect(help_text.get_by_role("button", name="Parar leitura", exact=True)).to_have_count(0)
    assert page.evaluate("testSpeech.queue.length") == 0
    page.get_by_text("Conferência e fecho", exact=True).click()

    # A calculator's explanation supports the same controls and stops on close.
    page.locator(".local-debit-button").first.click()
    calculator = page.get_by_role("dialog", name="Calculadora", exact=True)
    calculator.get_by_role("button", name="Informação da calculadora", exact=True).click()
    settings = calculator.get_by_text("Voz e velocidade", exact=True)
    settings.focus()
    page.keyboard.press("Enter")
    expect(calculator).to_be_visible()
    calculator.get_by_label("Velocidade da leitura", exact=True).focus()
    previous = calculator.get_by_label("Valor introduzido", exact=True).inner_text()
    page.keyboard.press("1")
    expect(calculator.get_by_label("Valor introduzido", exact=True)).to_have_text(previous)
    calculator.get_by_role("button", name="Ouvir informação", exact=True).click()
    expect(calculator.get_by_role("button", name="Parar leitura", exact=True)).to_be_visible()
    calculator.get_by_role("button", name="Fechar teclado", exact=True).click()
    assert page.evaluate("testSpeech.queue.length") == 0

    # A stopped paragraph pause must never restart speech in the background.
    listen.click()
    page.evaluate("testSpeech.queue.shift().onend({})")
    help_text.get_by_role("button", name="Parar leitura", exact=True).click()
    page.wait_for_timeout(400)
    assert page.evaluate("testSpeech.queue.length") == 0

    # Voices may arrive asynchronously, or be unavailable during startup.
    page.evaluate("testSpeech.voices = []; speechSynthesis.dispatchEvent(new Event('voiceschanged'))")
    help_text.get_by_text("Voz e velocidade", exact=True).click()
    expect(voice_select.locator("option")).to_have_count(1)
    listen.click()
    assert page.evaluate("testSpeech.spoken.at(-1).lang") == "pt-PT"
    help_text.get_by_role("button", name="Parar leitura", exact=True).click()
    page.evaluate("testSpeech.voices.push({name: 'Voz tardia Natural', lang: 'pt-PT', localService: false}); speechSynthesis.dispatchEvent(new Event('voiceschanged'))")
    expect(voice_select.locator("option")).to_have_count(2)
    listen.click()
    assert page.evaluate("testSpeech.spoken.at(-1).voice") == "Voz tardia Natural"
    help_text.get_by_role("button", name="Parar leitura", exact=True).click()

    # Mobile layout and page navigation cleanup.
    page.set_viewport_size({"width": 390, "height": 844})
    listen.click()
    help_text.screenshot(path=str(output / "conference-mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.get_by_role("button", name="Dashboard", exact=True).click()
    assert page.evaluate("testSpeech.queue.length") == 0
    assert not errors, errors
    page.close()

    # An unsupported browser still shows the full explanation.
    page = browser.new_page(locale="pt-PT")
    page.add_init_script("Object.defineProperty(window, 'speechSynthesis', {configurable: true, value: undefined});")
    page.route("**/api/**", mock_api)
    enter_cash(page)
    page.get_by_role("button", name="Informação sobre a conferência do faturado", exact=True).click()
    help_text = page.locator(".local-cash-conference-help")
    expect(help_text).to_contain_text("Guardar o faturado não cria movimentos.")
    expect(help_text.get_by_role("button", name="Ouvir informação", exact=True)).to_be_disabled()
    expect(help_text.get_by_role("status")).to_have_text("Este navegador não permite leitura em voz alta.")
    browser.close()

print("PASS: natural pt-PT voice preference, sentences/paragraphs and pronunciation, saved voice/speed, delayed voices, pause cancellation, calculator keyboard, lifecycle/errors/mobile/fallback; simulated speech only, no API writes.")
