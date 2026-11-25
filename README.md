# 🎤 Stage Presenter

An AI presentation trainer. Upload a `.pptx` with speaker notes and Stage Presenter will:

1. **Write the talk**: turn each slide's notes and text into polished stage narration, in the tone, style and length you pick.
2. **Present it**: speak the narration in the voice and English accent you choose, highlight each sentence in a teleprompter, and advance the slides.
3. **Coach you**: record your own take of a slide and get a score, pace, filler words, missed points and written coaching.

It can run **fully offline on an Intel AI PC** (OpenVINO on NPU / GPU / CPU), or use **Claude** (Anthropic API or Amazon Bedrock), **Gemini**, or any **OpenAI-compatible endpoint** for the language model.

## Contents

- [What runs where](#what-runs-where)
- [Requirements](#requirements)
- [Setup](#setup)
- [Choose the language model](#choose-the-language-model)
- [Using the app](#using-the-app)
- [⚙ Settings reference](#-settings-reference)
- [Voices and accents](#voices-and-accents)
- [Configuration reference (`.env`)](#configuration-reference-env)
- [Troubleshooting](#troubleshooting)
- [Performance notes](#performance-notes)
- [Project layout](#project-layout)
- [Tests](#tests)

## What runs where

| Function | Engine | Runs on |
|---|---|---|
| Script writing + coaching | OpenVINO GenAI · `Qwen3-8B-int4-cw-ov` | local **NPU**, falling back to GPU, then CPU |
| | Claude (Anthropic API or Amazon Bedrock), Gemini, or an OpenAI-compatible endpoint | cloud / your server |
| Rehearsal transcription | OpenVINO GenAI · `whisper-small.en-int8-ov` | local **Arc GPU** (or NPU / CPU) |
| | Gemini, or an OpenAI-compatible transcription endpoint | cloud / your server |
| Voices (offline) | Kokoro 82M (ONNX), Piper (ONNX) | CPU |
| Voices (online) | Microsoft Edge neural voices (all English accents incl. en-IN) | cloud |
| Voices (fallback) | Browser / Windows SAPI voices | local |
| Slide images | PowerPoint COM (LibreOffice as fallback) | local |

## Requirements

| | |
|---|---|
| **OS** | Windows 11 (the launcher `run.bat` and PowerPoint rendering are Windows-specific; the Python code itself is portable) |
| **Python** | Developed and tested on Python 3.14. [`uv`](https://docs.astral.sh/uv/) is used if installed, otherwise `venv` + `pip` |
| **Slide images** | Microsoft PowerPoint installed (best fidelity). Fallback: LibreOffice plus `pip install pymupdf`. With neither, slides are shown as text |
| **Browser** | Any current Chromium browser (Edge, Chrome). Microphone access needs `localhost` or HTTPS |
| **Disk** | About 5.5 GB for the full offline model set (LLM ≈ 5 GB). Much less if you use a cloud language model |
| **Hardware for local AI** | Intel Core Ultra with NPU and Arc GPU is ideal. Any machine works on CPU, more slowly |
| **Network** | Needed for model download, Edge voices and cloud language models. `HTTPS_PROXY` is honoured everywhere |

## Setup

### Option A: one command (Windows)

```bat
git clone https://github.com/ragural/stage_presenter.git
cd stage_presenter
run.bat
```

On the first run `run.bat`:

1. creates `.venv` and installs `requirements.txt`,
2. copies `.env.example` to `.env`,
3. downloads the offline models into `models/` (about 5.5 GB, one time),
4. opens http://127.0.0.1:8000 and starts the server.

Later runs skip straight to step 4. Stop the server with `Ctrl+C` in its window.

The **first** NPU compile of the local LLM takes a few minutes. The result is cached in `models/cache`, so later startups take seconds. The status pills in the top bar show when the LLM and Whisper are ready.

### Option B: step by step

```bat
git clone https://github.com/ragural/stage_presenter.git
cd stage_presenter

:: 1. environment
uv venv .venv && uv pip install --python .venv -r requirements.txt
::    without uv:  python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt

:: 2. configuration
copy .env.example .env

:: 3. models (pick what you need)
.venv\Scripts\python download_models.py                          :: everything
.venv\Scripts\python download_models.py --only kokoro piper whisper   :: skip the 5 GB LLM (use a cloud LLM instead)

:: 4. run
.venv\Scripts\python app.py
```

Then open http://127.0.0.1:8000.

Optional hardware check: `.venv\Scripts\python smoke_test.py` runs an end-to-end test of the local models.

### Cloud-only setup (no local LLM)

If you only want Claude, Gemini or a remote endpoint to write the scripts:

1. Follow Option B, downloading with `--only kokoro piper whisper` (or nothing at all: Edge voices need no download, and speech recognition can be Gemini).
2. Set the provider in `.env` as shown in the next section.
3. Start with `.venv\Scripts\python app.py`. Note that `run.bat` downloads the full model set whenever `models\llm` is missing.

## Choose the language model

The language model writes the narration and the rehearsal coaching. Set the default in `.env`, or change it at any time in **⚙ Settings** (which saves to `data/settings.json` and takes precedence over `.env`).

### Local OpenVINO (default, offline)

```ini
LLM_PROVIDER=openvino
OV_LLM_DEVICE=NPU          # NPU | GPU | CPU (falls back automatically)
```

### Claude through Amazon Bedrock

```ini
LLM_PROVIDER=anthropic
CLAUDE_ACCESS=bedrock
AWS_REGION=us-east-2
AWS_BEARER_TOKEN_BEDROCK=<your Bedrock API key>
BEDROCK_MODEL=us.anthropic.claude-opus-5-5     # optional; this is the default for us-* regions
# CLAUDE_EFFORT=medium                          # low | medium | high | xhigh | max
```

- **Credentials.** A Bedrock API key (`AWS_BEARER_TOKEN_BEDROCK`, or pasted in ⚙ Settings) is the simplest. Without one, the standard AWS credential chain (profile, SSO, role) signs the requests; that route needs boto3, which `requirements.txt` installs through `anthropic[bedrock]`.
- **Endpoint and model ids.** The default *Runtime (InvokeModel)* endpoint takes inference-profile ids such as `us.anthropic.claude-opus-5-5` or `global.anthropic.claude-opus-5-5`. The *Mantle (Messages API)* endpoint (`BEDROCK_API=mantle`) takes `anthropic.claude-opus-5-5` and needs the `bedrock-mantle:CreateInference` IAM permission.
- **Claude Code variables are understood.** If your environment already has `CLAUDE_CODE_USE_BEDROCK=1` and `ANTHROPIC_DEFAULT_MODEL=us.anthropic.claude-opus-5-5`, the app selects Bedrock and that model without the `CLAUDE_ACCESS` / `BEDROCK_MODEL` lines.
- Your AWS account must have access to the model in that region. Billing goes to your AWS account.

### Claude through the Anthropic API

```ini
LLM_PROVIDER=anthropic
CLAUDE_ACCESS=anthropic
ANTHROPIC_API_KEY=<your key>
# CLAUDE_MODEL=claude-opus-5-5
```

### Gemini

```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=<your key>
# GEMINI_MODEL=gemini-2.5-flash
```

### Any OpenAI-compatible endpoint (Ollama, LM Studio, OpenVINO Model Server, NVIDIA NIM, OpenAI, Azure…)

```ini
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1:8b
# LLM_API_KEY=
```

### Check it works

Open **⚙ Settings**, pick the provider tab and press **▶ Test**. It sends one short request with the values currently in the form and shows the reply and timing, before you save anything. The pill in the top bar then shows the active model, for example `LLM · us.anthropic.claude-opus-5-5 · Claude · Bedrock`.

> **Secrets.** `.env` and `data/` are git-ignored. Never commit keys. Keys saved through ⚙ Settings stay on the server in `data/settings.json`; the UI only ever shows `••••last4`.

## Using the app

1. **Upload** a `.pptx` (button or drag and drop), or try `samples/sample_deck.pptx`. Old `.ppt` files must be saved as `.pptx` first. Previously uploaded decks are under **Recent decks**.
2. Pick the **delivery** in the left panel: tone (inspirational, authoritative, executive…), style (keynote, TED, boardroom, lecture…), length, audience and any extra direction.
3. Pick the **voice**: engine, English accent (India, US, UK, Australia…), female or male, pace and pitch. **Preview voice** plays a sample.
4. Press **▶** (or `Space`). The presenter writes the narration for the slide, speaks it, highlights each sentence in the teleprompter and, with **Auto-advance** on, moves to the next slide.
5. **✨ Generate full script** writes every slide up front. Do this before presenting to avoid a wait at each new slide. Then **Copy**, **✎ Edit**, **↻ Rewrite** a single slide, or export the script as `.md` / `.txt`.
6. **🎙 Rehearse** tab: record your own take of the current slide. You get:
   - a score, words per minute (120–165 is healthy), filler words, long pauses, key-point coverage and wording match against the script
   - the points you missed
   - your transcript with fillers highlighted
   - coaching from the language model: strengths, improvements and a polished rewrite of your weakest line
   - a history of your takes on each slide

### Player controls

| Control | Action |
|---|---|
| **▶ / ⏸** or `Space` | Play / pause. Pausing while a slide is still being written holds it until you press ▶ again |
| **■** or `Esc` | Stop |
| **◀ / ▶** or `←` / `→` (`PageUp` / `PageDown`) | Previous / next slide |
| Thumbnails | Jump to a slide |
| **⛶ Stage mode** or `F`, or double-click the slide | Fullscreen slides with captions |

### Trainer modes

- **Shadow mode**: the AI pauses after each sentence so you can repeat it in your own voice.
- **Hide script**: blurs the teleprompter so you rehearse from memory (hover to peek).
- **Captions**: shows the sentence being spoken under the slide.

### How the script is written

For each slide the model receives the slide title, the slide text and the speaker notes, plus the previous slide's title and last sentences so the talk flows. It is told to convey what is in the notes and slide and not to invent facts. Slides that are only an image with no notes give the model little to work with, so add speaker notes to those for a faithful script.

## ⚙ Settings reference

Click **⚙ Settings** in the top bar. The **Language model** and **Speech recognition** cards each offer:

| Choice | What you set |
|---|---|
| 🖥 **Local / Whisper** (OpenVINO) | **Device** (NPU, GPU, CPU) and **Model** (any folder in `models/llm` or `models/whisper`) |
| ✳ **Claude** (language model only) | **Access**: *Anthropic API* (API key, model) or *Amazon Bedrock* (AWS region, endpoint, Bedrock API key, model); **Effort** low → max |
| ✦ **Gemini** | API key and model. One key serves both the language model and speech recognition |
| 🌐 **Endpoint** | any OpenAI-compatible server: **URL**, **API key**, **model**. For the LLM, *Advanced* adds an extra request body, temperature and max tokens. Presets: Ollama, LM Studio, OpenVINO Model Server, NVIDIA NIM, OpenAI, Azure (LLM); OpenAI Whisper, OVMS, Speaches/faster-whisper, Groq (speech) |
| ⏻ **Off** | the LLM falls back to lightly polished notes; Rehearse is disabled |

- **▶ Test** checks the values currently in the form, before you save. For the LLM it asks for one sentence. For speech it synthesizes a test phrase with Piper and shows what was heard.
- **↻ List** fetches the models your key can use (Anthropic API, Gemini, endpoints). For Bedrock it shows the usual ids for the region, because Bedrock offers no model list through the SDK.
- **Save & apply** takes effect immediately. A local model or device change reloads in the background, and the status pills show progress; switching away unloads the old model and frees the NPU or GPU.
- Settings are stored in `data/settings.json` and override `.env`. Leave a key field blank to keep the saved key or use the environment variable, or press ✕ to remove the saved key.
- A key saved in ⚙ Settings takes precedence over the one in the environment.
- Endpoints on `localhost`, `*.local` or private LAN IPs bypass `HTTP(S)_PROXY`, so local servers such as Ollama are not routed through a corporate proxy.

### Claude specifics

- Claude is offered as the language model only. Its API accepts text, images and PDFs, not audio, so it cannot be the speech recogniser. Mix and match instead: Claude writes and coaches while local Whisper or Gemini transcribes.
- Sampling settings such as temperature are not sent, because current Claude models reject them. **Effort** controls depth and speed: *Low* is the fastest, *Medium* is the default.
- Refusal fallback is on. If a safety classifier declines a request, it is retried on a fallback model: by the API itself on the Anthropic API, and by the SDK on Bedrock (where that fallback model must also be enabled in your AWS account). A request that is still declined appears as a clear message in the UI.

### Privacy and billing

Local OpenVINO keeps everything on the machine. Claude, Gemini and remote endpoints are cloud services: slide text, notes and scripts (and, with Gemini speech, your recordings) leave the machine and are billed to your account. Edge neural voices send the narration text to Microsoft.

## Voices and accents

| Accent | Edge (online) | Kokoro (offline) | Piper (offline) |
|---|---|---|---|
| en-US / en-CA | ✅ | ✅ American voices | ✅ lessac, amy, ryan |
| en-GB / IE / AU / NZ / ZA / SG | ✅ native | British voices (closest) | ✅ alba, alan, northern male |
| **en-IN** | ✅ Neerja, Prabhat | ⚠ experimental (Hindi voices speaking English) | British (closest) |

For an authentic Indian English accent with no internet, use the **Browser / Windows** engine and install the "English (India)" voice in *Windows Settings → Time & language → Speech*.

If Edge voices are unreachable at startup, the app switches to offline Kokoro automatically.

## Configuration reference (`.env`)

`.env` holds the defaults; see `.env.example` for the full annotated list. Real environment variables win over `.env`, and ⚙ Settings (`data/settings.json`) wins over both.

| Variable | Purpose |
|---|---|
| `HOST`, `PORT` | Server address (default `127.0.0.1:8000`). `HOST=0.0.0.0` shares it on your LAN |
| `PRELOAD_MODELS` | `1` loads local models at startup so the first request is fast |
| `LLM_PROVIDER` | `openvino` \| `anthropic` \| `gemini` \| `openai` \| `none` |
| `OV_LLM_DEVICE`, `OV_LLM_MODEL_DIR` | Local LLM device and model folder |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_EXTRA_BODY` | OpenAI-compatible endpoint |
| `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` | Sampling for local / endpoint / Gemini models |
| `CLAUDE_ACCESS` | `anthropic` \| `bedrock` |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_EFFORT` | Claude through the Anthropic API; effort applies to Bedrock too |
| `AWS_REGION` (or `BEDROCK_REGION`), `AWS_BEARER_TOKEN_BEDROCK`, `BEDROCK_API`, `BEDROCK_MODEL` | Claude through Amazon Bedrock |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_STT_MODEL` | Gemini |
| `STT_PROVIDER` | `openvino` \| `gemini` \| `openai` \| `none` |
| `OV_WHISPER_DEVICE`, `OV_WHISPER_MODEL_DIR` | Local Whisper device and model folder |
| `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL` | OpenAI-compatible transcription endpoint |
| `MAX_UPLOAD_MB` | Upload size limit (default 200) |
| `ANTHROPIC_BASE_URL`, `ANTHROPIC_BEDROCK_BASE_URL`, `GEMINI_BASE_URL` | Point the SDKs at an API gateway |

Common changes:

- `OV_LLM_DEVICE=GPU`: use the Arc iGPU instead of the NPU. It is faster for long outputs; the NPU uses less power.
- Lighter local LLM: `python download_models.py --only llm --llm OpenVINO/Phi-3.5-mini-instruct-int4-cw-ov`, then pick it in ⚙ Settings.
- Bigger Whisper: `python download_models.py --only whisper --whisper OpenVINO/whisper-large-v3-turbo-int8-ov` (best with `OV_WHISPER_DEVICE=GPU`).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Claude: no Anthropic API key…` | Access is set to *Anthropic API* but no key is available. Add a key, or set **Access** to *Amazon Bedrock* if you reach Claude through AWS |
| `Claude on Bedrock: set the AWS region…` | No region. Fill **AWS region** in ⚙ Settings or set `AWS_REGION` in `.env` |
| `Claude on Bedrock: access denied - Authentication failed…` | The Bedrock API key is wrong or expired. A key saved in ⚙ Settings overrides `AWS_BEARER_TOKEN_BEDROCK`: press ✕ next to it and save to use the environment key |
| `…not authorized to perform: bedrock-mantle:CreateInference` | Your IAM identity lacks Mantle access. Use the *Runtime (InvokeModel)* endpoint |
| `Invocation of model ID … with on-demand throughput isn't supported` | The Runtime endpoint needs an inference-profile id: `us.anthropic.claude-…` or `global.anthropic.claude-…`, not `anthropic.claude-…` |
| Works in my terminal, fails in the app | Variables set in one shell (`$env:…`, `set …`) are not visible to an app started elsewhere. Put them in `.env` or start the app from that shell |
| Changes in `.env` have no effect | ⚙ Settings (`data/settings.json`) overrides `.env`. Change the value in ⚙ Settings, or delete the entry from that file |
| A long wait at each new slide | The narration is written on demand. Use **✨ Generate full script** first, or lower Claude's **Effort** to *Low* |
| The talk keeps playing after the server is stopped | The browser tab already holds the audio. Press **■** or close the tab |
| The page behaves oddly after an update | Hard-refresh the tab with `Ctrl+F5` |
| Slides show as text instead of images | PowerPoint (or LibreOffice + PyMuPDF) was not available when the deck was uploaded. Install it and upload the deck again |
| LLM pill stuck on "loading…" | The first NPU compile takes a few minutes. Later starts use the cache in `models/cache` |
| Filler words are not counted | Whisper on the NPU drops them. Use `OV_WHISPER_DEVICE=GPU` (the default) |
| Edge voices unavailable | They need internet access. Use Kokoro, Piper or the Browser engine offline |
| Microphone blocked | Browsers allow the microphone only on `localhost` or HTTPS |
| Local server (Ollama etc.) returns 403 behind a proxy | Use `localhost` or a private IP in the endpoint URL; those bypass `HTTP(S)_PROXY` |

## Performance notes

Measured on an Intel Core Ultra X7 368H (Panther Lake):

| Task | Result |
|---|---|
| LLM load, NPU (first compile / cached) | ~147 s / a few seconds |
| One slide of narration, Qwen3-8B on NPU | ~8 s |
| Coaching feedback, Qwen3-8B on NPU | ~15 s |
| Whisper, 28 s take: Arc GPU / CPU / NPU | 2.0 s / 3.5 s / 0.3–0.8 s |
| Piper / Kokoro TTS | ~7× / ~1.5–2× faster than real time |
| One slide of narration, Claude Opus 5.5 on Bedrock, Medium effort | ~15 s including voice synthesis |

**Why Whisper defaults to the GPU:** Whisper normally removes "um" and "uh" from transcripts. A disfluent prompt makes it keep them, but the NPU's fixed tensor shapes reject that prompt. `OV_WHISPER_DEVICE=NPU` still works and is the fastest option, but filler counts will come out low. Running Whisper on the GPU also leaves the NPU free for the LLM.

The UI prefetches the next slide's narration and audio while the current one plays.

## Project layout

```
app.py                      FastAPI server + REST API
run.bat                     one-command setup and launch (Windows)
download_models.py          model downloader (Hugging Face mirrors, proxy-friendly retries)
smoke_test.py               end-to-end check of the local models
bench_devices.py            Whisper / Kokoro benchmark across devices
.env.example                annotated configuration defaults
presenter/pptx_parser.py    slide titles, text, speaker notes
presenter/slide_render.py   PowerPoint / LibreOffice → PNG
presenter/settings.py       runtime settings (⚙ Settings, data/settings.json)
presenter/llm.py            language-model dispatcher: OpenVINO, Claude, Gemini, OpenAI-compatible
presenter/claude_llm.py     Claude through the Anthropic API or Amazon Bedrock
presenter/gemini_ai.py      Gemini language model and transcription
presenter/script_writer.py  speechwriter prompts (tone, style, length, locale)
presenter/tts.py            voice engine dispatcher (Edge / Kokoro / Piper)
presenter/offline_tts.py    Kokoro + Piper with sentence timings
presenter/rehearsal.py      Whisper, delivery metrics, AI coaching
static/                     single-page UI (index.html, app.js, styles.css)
samples/sample_deck.pptx    a three-slide deck to try
tests/                      API and browser tests, mock servers
data/                       your decks, audio cache and settings (git-ignored)
models/                     downloaded models (git-ignored)
```

## Tests

The browser tests drive an installed Microsoft Edge through Playwright, from a separate environment:

```bat
uv venv ..\.venv-uitest && uv pip install --python ..\.venv-uitest playwright
```

```bat
:: end-to-end with the local models; the app must be running on :8000
..\.venv-uitest\Scripts\python tests\ui_test.py

:: settings / endpoints, against a mock OpenAI-compatible server
.venv\Scripts\python tests\mock_openai.py 9011              :: separate terminal
.venv\Scripts\python tests\settings_api_test.py
..\.venv-uitest\Scripts\python tests\settings_ui_test.py

:: Claude (Anthropic API + Bedrock) and Gemini through the real SDKs, against a mock of the APIs (no keys, no cost)
.venv\Scripts\python tests\mock_cloud.py 9012               :: separate terminal
set STAGE_SETTINGS_FILE=data\settings_test.json& set ANTHROPIC_BASE_URL=http://127.0.0.1:9012& set ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:9012/bedrock& set GEMINI_BASE_URL=http://127.0.0.1:9012/gemini& set NO_PROXY=127.0.0.1& set PRELOAD_MODELS=0& set PORT=8012& .venv\Scripts\python app.py
.venv\Scripts\python tests\cloud_api_test.py                :: 36 checks, incl. wire format (effort, fallbacks, Bedrock, audio part)
..\.venv-uitest\Scripts\python tests\cloud_ui_test.py        :: 19 browser checks
```

`ui_test.py` covers upload, script generation, playback with highlighting, auto-advance, Kokoro and Piper voices, editing, export and a full rehearsal. For the rehearsal it uses a fake microphone that plays a Piper voice reading the script with "um"s and one skipped sentence.

The API tests upload a throwaway copy of `samples/sample_deck.pptx` and delete it afterwards, so they do not write into your own decks. `STAGE_SETTINGS_FILE` keeps test settings out of `data/settings.json`.
