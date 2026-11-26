# KTU Study Buddy

KTU Study Buddy is a study workspace for KTU B.Tech students following the 2024 scheme. Organize your subjects, ask questions, revise from your materials, and practise for exams.

**[Open the live app](https://ktu-study-buddy.onrender.com/)**

Create an account with a username and password or continue with GitHub. Each account has a private workspace. Start with the sample Algorithms subject or add your own.

## What you can do

- Ask open-ended questions in **Study Chat** without uploading anything. Add an AI provider key in **Settings** for live explanations and follow-up questions.
- Upload syllabi, notes, textbooks, and past papers. Generate revision notes with references to uploaded pages and export them as Markdown or PDF.
- Work through short lessons in **Teacher Mode**. Practise with quizzes, flashcards, and timed mock exams, or submit written answers for feedback.
- Set exam dates, plan study sessions, and use available past-paper evidence to help prioritize revision.

## Host for multiple students

The live app runs on Render with persistent PostgreSQL storage on Neon. The local edition stores your library, notes, conversations, and progress in SQLite. See the [hosting guide](deploy/README.md) for deployment and backups.

For free managed hosting, use the [Render and Neon deployment guide](deploy/RENDER.md).

## Run locally

Install Python 3.12 or 3.13 and Node.js 22 with npm. From the project folder, run:

```powershell
python start.py
```

Open [KTU Study Buddy](http://127.0.0.1:8000/). The first run installs dependencies and builds the interface. Press Ctrl+C in the terminal to stop the server. The app binds to loopback.

Select **Load sample subject** to try an illustrative Algorithms syllabus and an original reference. The sample contains no student notes or past papers. Generate a topic note, open its cited pages, resume a lesson, and try the 60-mark mock under **Practice**. The Predictions view explains why confidence is low without matched past papers.

## Configure AI, search, and OCR

- Copy `.env.example` to `.env` for local configuration overrides. The app starts in labelled mock mode by default. Mock AI uses authored demo material and real retrieved excerpts. Mock search returns an empty set.
- Under **Settings**, add an API key and model ID for Gemini, OpenAI, Groq, OpenRouter, Mistral, or a custom OpenAI-compatible endpoint. Use **Test / list models** to check the key and browse model IDs. Set `BUDDY_MOCK=0` and restart for live generation. Enable structured output on models used for notes, lessons, quizzes, and grading. Configure vision for image questions and set priority and limits per provider.
- For web searches, provide `TAVILY_API_KEY`, set `SEARCH_MOCK=0`, and restart. Paper search lists candidates for approval before downloading. It checks public URLs and robots rules. Source metadata can be reviewed in Exam Prep.
- Install the Tesseract executable for local OCR. Put it on PATH or set `TESSERACT_CMD` to its full path. Low-confidence pages can be reviewed against the original file and then corrected in Subject Library. Handwriting accuracy depends on the page and the OCR engine.
- Embeddings use the local BGE-small-en-v1.5 model through FastEmbed and sqlite-vec. The model downloads once when first needed. Lexical retrieval is available when weights cannot be loaded, or when `BUDDY_EMBEDDINGS=lexical`. Upload files again to create semantic vectors for documents previously indexed in lexical mode.
- Provider keys are encrypted in the SQLite database with a key file under `.study-data`. Keep that directory private. When live AI or search is enabled, relevant prompts or excerpts go to the chosen service. Review its pricing and data terms before using it.

## Study workflow

1. Add a subject with its exam date. Upload a syllabus and confirm the editable outline, topics, course outcomes, textbooks, and exam pattern. Set question counts and choices for each module or for the whole paper.
2. Upload notes, textbooks and past papers. Open file and page references from generated notes or chat. For papers, correct detected session, year, and scheme where needed. The app displays unknown metadata as unknown.
3. Generate notes for a topic or module. Student edits and generated versions are saved independently. Export a revision to Markdown or PDF. The browser renders Mermaid and KaTeX. In the PDF, Mermaid and LaTeX are exported as text.
4. Use Teacher Mode for short lessons and follow-up explanations. Use Study Chat to retrieve notes and search the web, or to trigger a study action with commands such as `/notes`, `/quiz`, `/predictions`, `/grade`, and `/plan`. Changes are confirmed through the relevant study screen.
5. Use exam priorities as a study aid. The evidence includes papers, page references, a scheme discount and a holdout score when enough dated papers are available. Edit weights in `backend/prediction_weights.json` and restart.
6. Take a mock, review flashcards, or submit a written or photographed answer for grading. Review OCR before grading a photograph. Adjust available hours in Study Planner and build or re-plan the day.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run build
```

The tests cover the sample workflow, syllabus parsing, assessment totals, evidence scoring, citations, and simulated provider failures.

## Project layout

- `backend/` contains FastAPI routes, Alembic migrations, retrieval, provider adapters, and study services.
- `frontend/` contains the React, TypeScript, Vite, and Tailwind interface.
- `sample/` contains original demo content.
- `tests/` contains automated backend checks.
- `.study-data/` holds the database, model weights, upload originals, and encryption material. Git ignores it.

The implementation choices are recorded in [DECISIONS.md](DECISIONS.md). Provider and library references include [FastAPI](https://fastapi.tiangolo.com/tutorial/request-files/), [sqlite-vec](https://alexgarcia.xyz/sqlite-vec/features/knn.html), [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search), [Gemini](https://aistudio.google.com/docs/openai), [OpenAI](https://developers.openai.com/api/reference/resources/chat), [Groq](https://console.groq.com/docs/openai), [OpenRouter](https://openrouter.ai/docs/api_reference/overview), and [Mistral](https://docs.mistral.ai/inference/conversations/chat-completion).
